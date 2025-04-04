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

load_dotenv()

# Configuration
EVENTBRITE_URL = "https://www.eventbrite.com.au/o/the-royal-womens-hospital-14895986073"
STATE_FILE = "eventbrite_state.json"
CHECK_INTERVAL = 300  # 5 minutes in seconds

PUSHOVER_USER_KEY = "uta3qgo3z12jo2hbdatineo4psp849"
PUSHOVER_API_TOKEN = "au9v4g85ua4ckv5bxp7qt7fc5ob76q"

BABY_DUE_DATE = "2025-07-23"

# Create job directory with timestamp in AEST
aest = pytz.timezone('Australia/Sydney')
now = datetime.now(aest)
start_date = now.strftime("%Y-%m-%d")
start_time = now.strftime("%H-%M-%S")
job_dir = f"jobs/{start_date}_{start_time}"

# Create directory if it doesn't exist
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
                "token": PUSHOVER_API_TOKEN,
                "user": PUSHOVER_USER_KEY,
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

    # Optional: Run in headless mode (no visible browser window)
    # Uncomment the next line for headless mode
    # chrome_options.add_argument("--headless")

    # These options help with stability and avoiding detection
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    # Set a realistic user agent
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    # Create the driver (adjust path to your chromedriver location)
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def fetch_and_parse():
    """Fetch the Eventbrite page, click 'Upcoming', and parse the event cards."""
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
            logger.warning("Timeout waiting for future events container. Trying alternative approach...")


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
                model="gpt-4",  # or any other appropriate model
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

    There are two cases:
        - New classes before the target class have become available, after being Sold Out.
        - The target class has become available, as it is currently Not Yet On Sale.

    The target class is the one that has title containing: "July 5 + 12"
    """

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

    logger.info("No push notification sent")
    return None

def main():
    """Main function."""

    # Fetch and parse the Eventbrite page
    classes = fetch_and_parse()
    logger.info(f"Scraped {len(classes)} events")

    # Write out the raw results to JSON
    raw_json_path = os.path.join(job_dir, "eventbrite_classes_raw.json")
    with open(raw_json_path, "w") as f:
        json.dump(classes, f, indent=2)

    # # Read back in the raw results for further processing
    # with open("eventbrite_classes_raw.json", "r") as f:
    #     classes = json.load(f)

    # Use OpenAI to enrich the classes data
    classes_enriched = enrich_classes(classes)
    logger.info(f"Tidied {len(classes_enriched)} events")

    # Write results out to JSON
    enriched_json_path = os.path.join(job_dir, "eventbrite_classes_enriched.json")
    with open(enriched_json_path, "w") as f:
        json.dump(classes_enriched, f, indent=2)
    logger.info("Wrote results to eventbrite_classes.json")


    # # Read back in the enriched results for decision-making
    # with open("eventbrite_classes_enriched.json", "r") as f:
    #     classes_enriched = json.load(f)

    # Decide whether to send a push notification
    decision_function(classes_enriched)

    logger.info("Done")

if __name__ == "__main__":
    main()
