import time
from typing import Dict

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


def return_ip_information() -> Dict[str, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.ipburger.com/")
        time.sleep(5)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        ip_address = soup.find("span", id="ipaddress1")
        country = soup.find("strong", id="country_fullname")
        location = soup.find("strong", id="location")
        isp = soup.find("strong", id="isp")
        hostname = soup.find("strong", id="hostname")
        ip_type = soup.find("strong", id="ip_type")
        version = soup.find("strong", id="version")

        browser.close()

        return {
            "ip_address": ip_address.text if ip_address else "",
            "country": country.text if country else "",
            "location": location.text if location else "",
            "isp": isp.text if isp else "",
            "hostname": hostname.text if hostname else "",
            "type": ip_type.text if ip_type else "",
            "version": version.text if version else "",
        }

