#!/usr/bin/env python3
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import openai
import http.client
import urllib.parse
import os
import json
import time
import logging
import pytz
from datetime import datetime
from dotenv import load_dotenv
import shutil
from pathlib import Path
import tempfile

load_dotenv()

# Configuration
EVENTBRITE_URL = "https://www.eventbrite.com.au/o/the-royal-womens-hospital-14895986073"
STATE_FILE = "eventbrite_state.json"

CHECK_INTERVAL = 300

BABY_DUE_DATE = "2025-07-23"

# Create job directory if it doesn't exist
now = datetime.now(pytz.timezone('Australia/Sydney'))
start_date = now.strftime("%Y-%m-%d")
start_time = now.strftime("%H-%M-%S")
job_dir = f"jobs/{start_date}_{start_time}"

os.makedirs(job_dir, exist_ok=True)

# Setup logging to the job directory
log_file = f"{job_dir}/class-monitor.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def send_pushover_notification(message, title="Eventbrite Class Alert"):
    """Send a push notification using Pushover."""
    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request("POST", "/1/messages.json",
            urllib.parse.urlencode({
                "user": os.environ.get("PUSHOVER_USER_KEY"),
                "token": os.environ.get("PUSHOVER_API_TOKEN"),
                "title": title,
                "message": message,
                "priority": 1
            }), {"Content-type": "application/x-www-form-urlencoded"})
        response = conn.getresponse()
        result = response.read()
        logger.info(f"Pushover API response: {result}")
        return result
    except Exception as e:
        logger.error(f"Error sending Pushover notification: {e}")
        return None

def setup_driver():
    """Set up and return a Selenium WebDriver instance."""
    chrome_options = Options()

    # Essential options for headless operation
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    
    # Minimal window size
    chrome_options.add_argument("--window-size=1280,720")
    
    # Basic user agent
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    # Create the driver
    service = Service('/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def fetch_and_parse():
    """Fetch the Eventbrite page, click 'Upcoming' multiple times, and parse the event cards."""
    driver = setup_driver()
    classes = []

    try:
        # Navigate to the Eventbrite page
        logger.info(f"Navigating to Eventbrite page: {EVENTBRITE_URL}")
        driver.get(EVENTBRITE_URL)

        # Wait for the page to load
        logger.info("Waiting for page to load...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Click the "Show more" button as many times as needed
        show_more_clicked = 0
        logger.info("Clicking 'Show more' buttons to load all events")
        max_clicks = 5

        for _ in range(max_clicks):
            try:
                # Scroll down to make sure the button is visible
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)

                # Look for the "Show more" button
                show_more_buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Show more')]")

                if not show_more_buttons:
                    logger.info("No more 'Show more' buttons found.")
                    break

                # Try clicking the button
                button = show_more_buttons[0]
                try:
                    driver.execute_script("arguments[0].click();", button)
                    show_more_clicked += 1
                    time.sleep(3)  # Wait for new content to load
                    continue
                except Exception as e:
                    logger.warning(f"JavaScript click failed: {e}")

                # Wait for new content to load
                time.sleep(3)

                # Count current cards to verify loading
                current_cards = driver.find_elements(By.CSS_SELECTOR, "div[class*='event-card']")
                logger.info(f"After clicking 'Show more' {show_more_clicked} time(s), found {len(current_cards)} cards")

            except Exception as e:
                logger.warning(f"Error clicking 'Show more' button: {e}")
                break

        logger.info(f"Clicked 'Show more' button {show_more_clicked} time(s)")

        # Give the page one final pause to finish any loading
        time.sleep(2)

        # Wait for event cards to load
        logger.info("Waiting for the events grid container to load...")
        try:
            events_grid = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.organizer-profile__event-renderer__grid"))
            )
            logger.info("Found events grid container")

            # Count direct child divs with event-card class in their attributes
            event_cards = events_grid.find_elements(By.XPATH, "./div[contains(@class, 'event-card')]")
            total_cards = len(event_cards)
            logger.info(f"Found {total_cards} event cards in the grid")

        except TimeoutException:
            logger.warning("Timeout waiting for future events container.")

        # Parse each event card
        logger.info(f"Parsing {len(event_cards)} event cards...")
        for i, card in enumerate(event_cards, 1):
            try:
                # Get event status
                try:
                    badge = card.find_element(By.CSS_SELECTOR, ".event-card-badge p")
                    status = badge.get_attribute("textContent").strip()
                except NoSuchElementException:
                    status = "Available"

                # Get event title
                try:
                    # Updated selector to match the HTML structure
                    title_elem = card.find_element(By.CSS_SELECTOR, "h3.Typography_root__487rx")
                    title = title_elem.get_attribute("textContent").strip()
                except NoSuchElementException:
                    title = "Unknown Event"

                # Get event date
                try:
                    # Updated selector to match the HTML structure
                    # The date is in the first paragraph after the title
                    paragraphs = card.find_elements(By.CSS_SELECTOR, "section.event-card-details p.Typography_body-md__487rx")
                    date = paragraphs[0].get_attribute("textContent").strip()
                except (NoSuchElementException, IndexError):
                    date = "Unknown Date"

                event_info = {
                    "title": title,
                    "dates_detail": date,
                    "status": status,
                }
                classes.append(event_info)

            except Exception as e:
                logger.error(f"Error parsing event card {i}: {e}")
                continue

        # Debug information if no event cards found
        if not event_cards:
            logger.error("No event cards found. Taking screenshot for debugging...")
            driver.save_screenshot("debug_screenshot.png")

        return classes

    except Exception as e:
        logger.error(f"Error fetching or parsing Eventbrite page: {e}")
        return []

    finally:
        # Close the browser
        logger.info("Closing browser")
        driver.quit()

def enrich_classes(classes):
    """
    Enriches class data by extracting the first date from dates_detail using OpenAI API.

    Args:
        classes (list): List of dictionaries containing class information

    Returns:
        list: List of enriched class dictionaries with first_date field added
    """
    # Initialize OpenAI client
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    enriched_classes = []

    for class_info in classes:
        dates_detail = class_info.get("dates_detail", "")

        if not dates_detail:
            # If no dates_detail, add empty first_date and continue
            class_info["first_date"] = ""
            enriched_classes.append(class_info)
            continue

        # Call OpenAI API to extract the first date
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Extract the first date from the text and format it as YYYY-MM-DD. Only return the formatted date, nothing else."},
                    {"role": "user", "content": f"Extract the first date from this text: {dates_detail}"}
                ],
                temperature=0.0  # Use low temperature for deterministic outputs
            )

            # Extract the formatted date from the response
            first_date = response.choices[0].message.content.strip()

            # Validate the date format (basic check)
            try:
                datetime.strptime(first_date, "%Y-%m-%d")
            except ValueError:
                # If not in expected format, set to empty string
                first_date = ""

            # Add the first date to the class info
            class_info["first_date"] = first_date
            enriched_classes.append(class_info)

        except Exception as e:
            # Log error and continue with empty first_date
            print(f"Error extracting date for {dates_detail}: {e}")
            class_info["first_date"] = ""
            enriched_classes.append(class_info)

    return enriched_classes

def decision_function(classes):
    """
    Assesses the enriched classes data and decides whether to send a push notification.

    There are three cases:
        - Some classes have been added or removed, such that the number of classes is different to the last time we checked.
        - New classes before the target class have become available, after being Sold Out.
        - The target class has become available, as it is currently Not Yet On Sale.

    The target class is the one that has title containing: "July 5 + 12"
    """
    # Check for class count changes
    current_count = len(classes)
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        previous_count = state.get('class_count', 28)  # Default to 28 if not found
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("No state file found, defaulting to 28 classes")
        previous_count = 28  # Default to 28 if file doesn't exist or is invalid

    # Update state file with current count
    with open(STATE_FILE, 'w') as f:
        json.dump({'class_count': current_count}, f)

    # Send notification if count has changed
    if current_count != previous_count:
        message = f"Number of classes has changed from {previous_count} to {current_count}!"
        send_pushover_notification(message)
        logger.info(f"Sent push notification for class count change: {previous_count} -> {current_count}")

    # Check the target class status
    target_class_found = False
    for class_info in classes:
        class_title = class_info.get("title")
        if not class_title:
            continue
        if "July 5 + 12" in class_title:
            target_class_found = True
            if class_info.get("status") == "Available":
                message = "The target class is now available!"
                send_pushover_notification(message)
                logger.info("Sent push notification for available target class")
                return
    if not target_class_found:
        logger.warning("Target class not found in the data")

    # Check for new classes before the target class
    class_start_deadline = datetime.strptime(BABY_DUE_DATE, "%Y-%m-%d")
    interesting_classes = []
    for class_info in classes:

        first_date = class_info.get("first_date")
        if not first_date:
            continue

        class_title = class_info.get("title")
        if not class_title:
            continue

        bad_keywords = ["vietnam", "chinese", "lgbt", "online"]
        if any(keyword in class_title.lower() for keyword in bad_keywords):
            continue

        class_status = class_info.get("status")
        if not class_status:
            continue

        if class_status == "Available":
            first_date = class_info.get("first_date")
            if not first_date:
                continue

            class_date = datetime.strptime(first_date, "%Y-%m-%d")
            if class_date < class_start_deadline:
                interesting_classes.append(class_title)

    if interesting_classes:
        message = "New classes available before the deadline:\n" + "\n".join(interesting_classes)
        send_pushover_notification(message)
        logger.info("Sent push notification for new class(es) before target class")
        return

    return None

def cleanup_old_jobs():
    """Delete job directories that are more than 6 jobs old."""
    jobs_dir = Path("jobs")
    if not jobs_dir.exists():
        return

    # Get all job directories and sort them by creation time
    job_dirs = [d for d in jobs_dir.iterdir() if d.is_dir()]
    job_dirs = sorted(job_dirs, key=lambda x: x.stat().st_mtime)

    # Keep only the 6 most recent directories
    if len(job_dirs) > 6:
        for old_dir in job_dirs[:-6]:
            try:
                shutil.rmtree(old_dir)
                logger.info(f"Deleted old job directory: {old_dir}")
            except Exception as e:
                logger.error(f"Error deleting {old_dir}: {e}")

def main():
    """Main function."""
    # Clean up old job directories first
    logger.info("Cleaning up old job directories")
    cleanup_old_jobs()

    # Fetch and parse the Eventbrite page
    logger.info("Fetching and parsing Eventbrite page")
    classes = fetch_and_parse()
    logger.info(f"Scraped {len(classes)} events")

    # Write out the raw results to JSON
    raw_json_path = os.path.join(job_dir, "eventbrite_classes_raw.json")
    with open(raw_json_path, "w") as f:
        json.dump(classes, f, indent=2)

    # Use OpenAI to enrich the classes data
    logger.info("Enriching classes data")
    classes_enriched = enrich_classes(classes)
    logger.info(f"Enriched {len(classes_enriched)} events")

    # Write results out to JSON
    enriched_json_path = os.path.join(job_dir, "eventbrite_classes_enriched.json")
    with open(enriched_json_path, "w") as f:
        json.dump(classes_enriched, f, indent=2)
    logger.info("Wrote results to eventbrite_classes_enriched.json")

    # Decide whether to send a push notification
    logger.info("Deciding whether to send a push notification")
    decision_function(classes_enriched)

    logger.info("Done")

if __name__ == "__main__":
    main()
