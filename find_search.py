import requests
from bs4 import BeautifulSoup

def find():
    html = requests.get("https://www.vasanthandco.in/").text
    soup = BeautifulSoup(html, "html.parser")
    inputs = soup.find_all("input")
    for i in inputs:
        print(i.get("id", ""), i.get("name", ""), i.get("type", ""), i.get("class", ""), i.get("placeholder", ""))

if __name__ == '__main__':
    find()