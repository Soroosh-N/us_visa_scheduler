import re
import time
import json
import random
import requests
import configparser
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from embassy import *
from utils import get_tomorrow

config = configparser.ConfigParser()
config.read('/Users/mkushka/Desktop/us_visa_scheduler/config.ini')

# Personal Info:
# Account and current appointment info from https://ais.usvisa-info.com
USERNAME = config['PERSONAL_INFO']['USERNAME']
PASSWORD = config['PERSONAL_INFO']['PASSWORD']
# Find SCHEDULE_ID in re-schedule page link:
# https://ais.usvisa-info.com/en-am/niv/schedule/{SCHEDULE_ID}/appointment
SCHEDULE_ID = config['PERSONAL_INFO']['SCHEDULE_ID']
GROUP_ID = config['PERSONAL_INFO']['GROUP_ID']
# Target Period:
PRIOD_START = config['PERSONAL_INFO']['PRIOD_START']
PRIOD_END = config['PERSONAL_INFO']['PRIOD_END']
# Embassy Section:
YOUR_EMBASSY = config['PERSONAL_INFO']['YOUR_EMBASSY']
EMBASSY = Embassies[YOUR_EMBASSY][0]
FACILITY_ID = Embassies[YOUR_EMBASSY][1]
REGEX_CONTINUE = Embassies[YOUR_EMBASSY][2]

# Notification:
# Get email notifications via https://sendgrid.com/ (Optional)
SENDGRID_API_KEY = config['NOTIFICATION']['SENDGRID_API_KEY']
# Get push notifications via https://pushover.net/ (Optional)
PUSHOVER_TOKEN = config['NOTIFICATION']['PUSHOVER_TOKEN']
PUSHOVER_USER = config['NOTIFICATION']['PUSHOVER_USER']
# Get push notifications via PERSONAL WEBSITE http://yoursite.com (Optional)
PERSONAL_SITE_USER = config['NOTIFICATION']['PERSONAL_SITE_USER']
PERSONAL_SITE_PASS = config['NOTIFICATION']['PERSONAL_SITE_PASS']
PUSH_TARGET_EMAIL = config['NOTIFICATION']['PUSH_TARGET_EMAIL']
PERSONAL_PUSHER_URL = config['NOTIFICATION']['PERSONAL_PUSHER_URL']
# Get push notifications via Telegram
TELEGRAM_TOKEN = config['NOTIFICATION']['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = config['NOTIFICATION']['TELEGRAM_CHAT_ID']

# Time Section:
minute = 60
hour = 60 * minute
# Time between steps (interactions with forms)
STEP_TIME = 0.5
# Time between retries/checks for available dates (seconds)
RETRY_TIME_L_BOUND = config['TIME'].getfloat('RETRY_TIME_L_BOUND')
RETRY_TIME_U_BOUND = config['TIME'].getfloat('RETRY_TIME_U_BOUND')
# Cooling down after WORK_LIMIT_TIME hours of work (Avoiding Ban)
WORK_LIMIT_TIME = config['TIME'].getfloat('WORK_LIMIT_TIME')
WORK_COOLDOWN_TIME = config['TIME'].getfloat('WORK_COOLDOWN_TIME')
# Temporary Banned (empty list): wait COOLDOWN_TIME hours
BAN_COOLDOWN_TIME = config['TIME'].getfloat('BAN_COOLDOWN_TIME')

# CHROMEDRIVER
# Details for the script to control Chrome
LOCAL_USE = config['CHROMEDRIVER'].getboolean('LOCAL_USE')
# Optional: HUB_ADDRESS is mandatory only when LOCAL_USE = False
HUB_ADDRESS = config['CHROMEDRIVER']['HUB_ADDRESS']

SIGN_IN_LINK = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_in"
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment"
DATE_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
TIME_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
MAIN_URL = f"https://ais.usvisa-info.com/en-ca/niv/groups/{GROUP_ID}"
SIGN_OUT_LINK = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_out"

JS_SCRIPT = ("var req = new XMLHttpRequest();"
             f"req.open('GET', '%s', false);"
             "req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');"
             "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
             f"req.setRequestHeader('Cookie', '_yatri_session=%s');"
             "req.send(null);"
             "return req.responseText;")

NO_APPOINTMENT_TEXT = "There are no available appointments at the selected location. Please try again later."


def autodetect_period_start_and_end(LOG_FILE_NAME):
    if (len('PRIOD_START') != len('yyyy-mm-dd')):
        PRIOD_START = get_tomorrow()
    if (len('PRIOD_END') != len('yyyy-mm-dd')):
        driver.get(MAIN_URL)
        main_page = driver.find_element(By.ID, 'main')
        # # For debugging
        # with open('debugging/main_page', 'w') as f:
        #     f.write(main_page.text)
        # Look for the current appointment date
        match = re.search(r"\b\d{1,2} [a-zA-Z]+, \d{4}\b", main_page.text)
        if match:
            found_date = match.group(0)
            date_obj = datetime.strptime(found_date, '%d %B, %Y')
            PRIOD_END = date_obj.strftime('%Y-%m-%d')
        else:
            # Error loading current appointment date
            msg = "Error loading current appointment date! Set PRIOD_END variable in config.ini manually!\n"
            END_MSG_TITLE = "EXCEPTION"
            info_logger(LOG_FILE_NAME, msg)
            send_notification(END_MSG_TITLE, msg)
            notify_in_telegram(END_MSG_TITLE + "! " + msg)
            driver.get(SIGN_OUT_LINK)
            driver.stop_client()
            driver.quit()

    return PRIOD_START, PRIOD_END


def notify_in_telegram(msg):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    parameters = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg}
    return requests.post(url, parameters)


def send_notification(title, msg):
    print(f"Sending notification!")
    if SENDGRID_API_KEY:
        message = Mail(from_email=USERNAME, to_emails=USERNAME,
                       subject=msg, html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            print(response.status_code)
            print(response.body)
            print(response.headers)
        except Exception as e:
            print(e.message)
    if PUSHOVER_TOKEN:
        url = "https://api.pushover.net/1/messages.json"
        data = {
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "message": msg
        }
        requests.post(url, data)
    if PERSONAL_SITE_USER:
        url = PERSONAL_PUSHER_URL
        data = {
            "title": "VISA - " + str(title),
            "user": PERSONAL_SITE_USER,
            "pass": PERSONAL_SITE_PASS,
            "email": PUSH_TARGET_EMAIL,
            "msg": msg,
        }
        requests.post(url, data)


def auto_action(label, find_by, el_type, action, value, sleep_time=0):
    print("\t" + label + ":", end="")
    # Find Element By
    match find_by.lower():
        case 'id':
            item = driver.find_element(By.ID, el_type)
        case 'name':
            item = driver.find_element(By.NAME, el_type)
        case 'class':
            item = driver.find_element(By.CLASS_NAME, el_type)
        case 'xpath':
            item = driver.find_element(By.XPATH, el_type)
        case _:
            return 0
    # Do Action:
    match action.lower():
        case 'send':
            item.send_keys(value)
        case 'click':
            item.click()
        case _:
            return 0
    print("\t\tCheck!")
    if sleep_time:
        time.sleep(sleep_time)


def start_process():
    # Bypass reCAPTCHA
    driver.get(SIGN_IN_LINK)
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
    auto_action("Click bounce", "xpath",
                '//a[@class="down-arrow bounce"]', "click", "", STEP_TIME)
    auto_action("Email", "id", "user_email", "send", USERNAME, STEP_TIME)
    auto_action("Password", "id", "user_password", "send", PASSWORD, STEP_TIME)
    auto_action("Privacy", "class", "icheckbox", "click", "", STEP_TIME)
    auto_action("Enter Panel", "name", "commit", "click", "", STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located(
        (By.XPATH, "//a[contains(text(), '" + REGEX_CONTINUE + "')]")))
    print("\n\tlogin successful!\n")
    notify_in_telegram('The bot has started successfully')


def no_appointment_check():
    driver.get(APPOINTMENT_URL)
    time.sleep(3)

    # # For debugging
    # with open('debugging/page_source.html', 'w', encoding='utf-8') as f:
    #     f.write(driver.page_source)

    # Getting main text
    main_page = driver.find_element(By.ID, 'main')

    # # For debugging
    # with open('debugging/main_page', 'w') as f:
    #     f.write(main_page.text)

    # If the "no appointment" text is not found return True. A change was found.
    return NO_APPOINTMENT_TEXT in main_page.text


def reschedule(date):
    print("rescheduling")
    time = get_time(date)
    driver.get(APPOINTMENT_URL)
    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Referer": APPOINTMENT_URL,
        "Cookie": "_yatri_session=" + driver.get_cookie("_yatri_session")["value"]
    }
    data = {
        "utf8": driver.find_element(by=By.NAME, value='utf8').get_attribute('value'),
        "authenticity_token": driver.find_element(by=By.NAME, value='authenticity_token').get_attribute('value'),
        "confirmed_limit_message": driver.find_element(by=By.NAME, value='confirmed_limit_message').get_attribute('value'),
        "use_consulate_appointment_capacity": driver.find_element(by=By.NAME, value='use_consulate_appointment_capacity').get_attribute('value'),
        "appointments[consulate_appointment][facility_id]": FACILITY_ID,
        "appointments[consulate_appointment][date]": date,
        "appointments[consulate_appointment][time]": time,
    }
    r = requests.post(APPOINTMENT_URL, headers=headers, data=data)
    with open('debugging/main_page', 'w') as f:
        f.write(r.text)
    if(r.text.find('You have successfully scheduled your visa appointment') != -1):
        title = "SUCCESS"
        msg = f"Rescheduled Successfully! {date} {time}"
    else:
        title = "FAIL"
        msg = f"Reschedule Failed!!! {date} {time}"
    notify_in_telegram(msg)
    return [title, msg]


def get_date():
    # Requesting to get the whole available dates
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(DATE_URL), session)
    content = driver.execute_script(script)
    return json.loads(content)


def get_time(date):
    time_url = TIME_URL % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    content = driver.execute_script(script)
    data = json.loads(content)
    time = data.get("available_times")[-1]
    print(f"Got time successfully! {date} {time}")
    return time


def is_logged_in():
    content = driver.page_source
    if(content.find("error") != -1):
        return False
    return True


def get_available_date(dates):
    # Evaluation of different available dates
    def is_in_period(date, PSD, PED):
        new_date = datetime.strptime(date, "%Y-%m-%d")
        result = (PED > new_date and new_date > PSD)
        # print(f'{new_date.date()} : {result}', end=", ")
        return result

    PED = datetime.strptime(PRIOD_END, "%Y-%m-%d")
    PSD = datetime.strptime(PRIOD_START, "%Y-%m-%d")
    for d in dates:
        date = d.get('date')
        if is_in_period(date, PSD, PED):
            return date
    print(f"\n\nNo available dates between ({PSD.date()}) and ({PED.date()})!")


def info_logger(file_path, log):
    with open(file_path, "a") as file:
        file.write(str(datetime.now().time()) + ":\n" + log + "\n")


chrome_options = Options()
# chrome_options.add_argument("--disable-extensions")
# chrome_options.add_argument("--disable-gpu")
# chrome_options.add_argument("--no-sandbox") # linux only
# if os.getenv('HEADLESS') == 'True':
# Comment for visualy debugging
chrome_options.add_argument("--headless")
# chrome_options.add_argument('--version', '115.0.5790.114')

# Initialize the chromediver (must be installed and in PATH)
driver = webdriver.Chrome(options=chrome_options)

if __name__ == "__main__":
    first_loop = True
    while 1:
        LOG_FILE_NAME = "logs/" + "log_" + str(datetime.now().date()) + ".log"
        RETRY_WAIT_TIME = random.randint(
            RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND)

        # Out of service time
        current_time = datetime.datetime.now().time()
        # print(f"Current time: {current_time}")

        if current_time >= datetime.time(20, 0):
            msg = "Time is after 20:00, going to sleep."
            print(msg)
            info_logger(LOG_FILE_NAME, msg)
            notify_in_telegram(msg)

            # Calculate time until 15:40 next day
            now = datetime.datetime.now()
            next_day = now + datetime.timedelta(days=1)
            wake_up_time = datetime.datetime.combine(
                next_day.date(), datetime.time(15, 40))

            sleep_seconds = (wake_up_time - now).total_seconds()
            # print(f"Sleeping for {sleep_seconds} seconds.")

            time.sleep(sleep_seconds)

        if first_loop:
            t0 = time.time()
            total_time = 0
            Req_count = 0
            start_process()
            PRIOD_START, PRIOD_END = autodetect_period_start_and_end(
                LOG_FILE_NAME)
            print('PRIOD_START', PRIOD_START)
            print('PRIOD_END', PRIOD_END)
            first_loop = False
        Req_count += 1
        try:
            msg = "-" * 60 + \
                f"\nRequest count: {Req_count}, Log time: {datetime.today()}\n"
            print(msg)
            info_logger(LOG_FILE_NAME, msg)
            dates = get_date()
            if not dates:
                if no_appointment_check():
                    # No Appointments
                    msg = "There are no appointments available\n"
                    print(msg)
                    info_logger(LOG_FILE_NAME, msg)
                else:
                    # Ban Situation
                    msg = f"List is empty, Probably banned!\n\tSleep for {BAN_COOLDOWN_TIME} hours!\n"
                    print(msg)
                    notify_in_telegram(msg)
                    info_logger(LOG_FILE_NAME, msg)
                    send_notification("BAN", msg)
                    driver.get(SIGN_OUT_LINK)
                    time.sleep(BAN_COOLDOWN_TIME * hour)
                    first_loop = True
            else:
                # Print Available dates:
                msg = ""
                for d in dates:
                    msg = msg + "%s" % (d.get('date')) + ", "
                msg = "Available dates:\n" + msg
                print(msg)
                info_logger(LOG_FILE_NAME, msg)
                date = get_available_date(dates)
                if date:
                    # A good date to schedule for
                    END_MSG_TITLE, msg = reschedule(date)
                    # break
            t1 = time.time()
            total_time = t1 - t0
            msg = "\nWorking Time:  ~ {:.2f} minutes".format(
                total_time/minute)
            print(msg)
            info_logger(LOG_FILE_NAME, msg)
            if total_time > WORK_LIMIT_TIME * hour:
                # Let program rest a little
                send_notification(
                    "REST", f"Break-time after {WORK_LIMIT_TIME} hours | Repeated {Req_count} times")
                driver.get(SIGN_OUT_LINK)
                time.sleep(WORK_COOLDOWN_TIME * hour)
                first_loop = True
            else:
                msg = "Retry Wait Time: " + \
                    str(RETRY_WAIT_TIME) + " seconds"
                print(msg)
                info_logger(LOG_FILE_NAME, msg)
                time.sleep(RETRY_WAIT_TIME)
        except Exception as e:
            print('Error at %s', 'division', exc_info=e)
            # Exception Occured
            msg = f"Break the loop after exception!\n"
            END_MSG_TITLE = "EXCEPTION"
            break

print(msg)
info_logger(LOG_FILE_NAME, msg)
send_notification(END_MSG_TITLE, msg)
# driver.get(SIGN_OUT_LINK)
# driver.stop_client()
# driver.quit()
