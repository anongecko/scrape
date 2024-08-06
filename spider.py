import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.chrome.options import Options
import json
import logging
from bs4 import BeautifulSoup
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import TerminalFormatter
import sqlite3
from random import choice
import os
from scrape_settings import USER_AGENTS, CONCURRENT_REQUESTS, DOWNLOAD_DELAY, ROBOTSTXT_OBEY, MEMORY_LIMIT, CPU_LIMIT
import time
import hashlib
import re
from tqdm import tqdm
import keyboard
from github import Github
import asyncio
from aiohttp import ClientSession
from urllib.parse import urlparse, urljoin
import aiosqlite
from memory_profiler import profile
import psutil

# Set up logging
logging.basicConfig(
    filename='scraper.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Log scraped URLs to a separate file
scraped_urls_logger = logging.getLogger('scraped_urls')
scraped_urls_logger.setLevel(logging.INFO)
scraped_urls_handler = logging.FileHandler('scraped_urls.log')
scraped_urls_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
scraped_urls_logger.addHandler(scraped_urls_handler)

# Function to check if an element exists
def element_exists(driver, by, value):
    try:
        return driver.find_element(by, value) is not None
    except NoSuchElementException:
        return False

# Function to extract code blocks from HTML
def extract_code_blocks(html):
    soup = BeautifulSoup(html, 'html.parser')
    return [code_tag.get_text().strip() for code_tag in soup.find_all('code')]

# Function to clean text
def clean_text(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', re.sub(r'<[^>]+>', '', text))).lower()

# Function to detect code language
def detect_code_language(code):
    languages = ['python', 'solidity', 'rust', 'javascript', 'typescript', 'nextjs', 'react', 'nodejs']
    for language in languages:
        try:
            lexer = get_lexer_by_name(language)
            highlight(code, lexer, TerminalFormatter())
            return language
        except Exception as e:
            logging.debug(f"Error detecting language for code: {code[:50]}..., Error: {str(e)}")
    return 'unknown'

# Function to tokenize code
def tokenize_code(code, language):
    try:
        lexer = get_lexer_by_name(language)
        return list(lexer.get_tokens(code))
    except Exception as e:
        logging.debug(f"Error tokenizing code: {code[:50]}..., Error: {str(e)}")
        return []

# Asynchronous function to store data in a SQLite database
async def store_data_in_db(data):
    async with aiosqlite.connect('code_data.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS code_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                content TEXT,
                code_block TEXT,
                language TEXT,
                tokens TEXT,
                source_type TEXT
            )
        ''')

        for item in data:
            url = item['url']
            title = item['title']
            content = item['content']
            source_type = item.get('source_type', 'unknown')
            for code_block_item in item['code_blocks']:
                code_block = code_block_item['code']
                language = code_block_item['language']
                tokens = json.dumps(code_block_item['tokens'])
                await db.execute('''
                    INSERT INTO code_data (url, title, content, code_block, language, tokens, source_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (url, title, content, code_block, language, tokens, source_type))

        await db.commit()

# Function to handle basic CAPTCHA types
async def handle_captcha(driver):
    print("Processing captcha...")
    captcha_elements = driver.find_elements(By.XPATH, "//img[@alt='captcha'] | //img[@title='captcha'] | //div[@class='captcha'] | //div[@id='captcha']")
    if captcha_elements:
        captcha_input = driver.find_element(By.ID, 'captcha-input')
        captcha_solution = 'your_captcha_solution'  # Replace with actual captcha solving logic
        captcha_input.send_keys(captcha_solution)
        captcha_submit = driver.find_element(By.ID, 'captcha-submit')
        captcha_submit.click()
        print(f"Captcha solution: {captcha_solution}")
        return True
    
    recaptcha_elements = driver.find_elements(By.XPATH, "//iframe[@src*='recaptcha']")
    if recaptcha_elements:
        print("ReCAPTCHA detected. Handling...")
        # Implement ReCAPTCHA handling logic here
        return True
    
    return False

# Function to handle login prompts
async def handle_login(driver):
    login_elements = driver.find_elements(By.XPATH, "//form[@id='login-form'] | //form[@class='login-form']")
    if login_elements:
        username = input("Enter your username/email: ")
        password = input("Enter your password: ")
        username_field = driver.find_element(By.ID, 'username')
        password_field = driver.find_element(By.ID, 'password')
        username_field.send_keys(username)
        password_field.send_keys(password)
        login_button = driver.find_element(By.ID, 'login-button')
        login_button.click()
        return True
    return False

# Function to rotate user agents
def rotate_user_agent():
    return choice(USER_AGENTS)

@profile
class WebsiteSpider(scrapy.Spider):
    name = 'website_spider'
    start_urls = []
    visited_urls = set()
    content_hashes = set()
    total_links = 0
    scraped_links = 0
    base_url = None

    def __init__(self, *args, **kwargs):
        super(WebsiteSpider, self).__init__(*args, **kwargs)
        self.options = Options()
        self.options.add_argument("--headless=new")
        self.options.add_argument(f'user-agent={rotate_user_agent()}')
        self.driver = webdriver.Chrome(options=self.options)
        self.driver.set_page_load_timeout(30)
        self.all_data = []
        self.load_start_urls()
        os.makedirs('data', exist_ok=True)
        self.total_links = len(self.start_urls)
        self.pbar = tqdm(total=self.total_links, desc=f"Scraping Website")
        self.base_url = self.start_urls[0]
        self.sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.proxies = self.load_proxies()

    def load_start_urls(self):
        with open('links_to_scrape.txt', 'r') as f:
            self.start_urls = [line.strip() for line in f]

    def load_proxies(self):
        proxies = []
        try:
            with open('proxies.txt', 'r') as f:
                proxies = f.read().splitlines()
        except FileNotFoundError:
            logging.warning("proxies.txt not found. Using default proxies.")
            proxies = ['http://127.0.0.1:8080', 'socks5://127.0.0.1:9050']  # Replace with your default proxies
        return proxies

    def get_random_proxy(self):
        return choice(self.proxies)

    async def start_requests(self):
        async with ClientSession() as session:
            for url in self.start_urls:
                if 'github.com' in url:
                    yield scrapy.Request(url=url, callback=self.parse_github, meta={'session': session})
                else:
                    yield scrapy.Request(url=url, callback=self.parse, meta={'session': session})

    async def parse(self, response):
        if response.url in self.visited_urls:
            return

        self.visited_urls.add(response.url)
        async with self.sem:
            try:
                # Use a proxy for each request
                self.driver.execute_script(f"window.localStorage.setItem('proxy', '{self.get_random_proxy()}')")
                await self.driver.get(response.url)

                if await handle_login(self.driver):
                    await asyncio.sleep(5)

                if await handle_captcha(self.driver):
                    await asyncio.sleep(5)

                total_links = len(await self.driver.find_elements(By.TAG_NAME, 'a'))
                self.pbar.total = total_links
                self.pbar.set_description(f"Scraping {response.url}")

                await WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, 'body'))
                )

                await self.handle_infinite_scroll()
                await self.click_show_more_buttons()
                await self.click_code_toggles()

                data = await self.extract_data(response)
                
                if not self.is_valid_data(data):
                    return

                await store_data_in_db([data])

                await self.crawl_links(response)

            except TimeoutException:
                logging.warning(f'Timeout loading {response.url}')
                await self.retry_scraping(response)
            except Exception as e:
                logging.error(f'Error scraping {response.url}: {str(e)}')

    async def handle_infinite_scroll(self):
        last_height = await self.driver.execute_script("return document.body.scrollHeight")
        while True:
            await self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(2)
            new_height = await self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    async def click_show_more_buttons(self):
        buttons = await self.driver.find_elements(By.XPATH, "//button[contains(text(), 'Show more')]")
        for button in buttons:
            try:
                await button.click()
                await asyncio.sleep(1)
            except ElementClickInterceptedException:
                logging.warning(f"Element click intercepted at {self.driver.current_url}")
            except Exception as e:
                logging.error(f"Error clicking button at {self.driver.current_url}: {str(e)}")

    async def click_code_toggles(self):
        code_toggles = await self.driver.find_elements(By.XPATH, "//button[contains(text(), 'Code')] | //button[contains(text(), 'Show Code')] | //button[contains(text(), 'View Code')]")
        for toggle in code_toggles:
            try:
                await toggle.click()
                await asyncio.sleep(1)
            except ElementClickInterceptedException:
                logging.warning(f"Element click intercepted at {self.driver.current_url}")
            except Exception as e:
                logging.error(f"Error clicking code toggle at {self.driver.current_url}: {str(e)}")

    async def extract_data(self, response):
        data = {
            'url': response.url,
            'title': await self.driver.title,
            'content': clean_text(await (await self.driver.find_element(By.TAG_NAME, 'body')).text),
            'code_blocks': [],
            'source_type': self.determine_source_type(response.url)
        }
        
        for code_block in extract_code_blocks(await self.driver.page_source):
            language = detect_code_language(code_block)
            tokens = tokenize_code(code_block, language)
            data['code_blocks'].append({
                'code': code_block,
                'language': language,
                'tokens': tokens
            })
        
        return data

    def determine_source_type(self, url):
        if 'docs' in url or 'documentation' in url:
            return 'documentation'
        elif 'blog' in url:
            return 'blog'
        elif 'book' in url:
            return 'book'
        else:
            return 'website'

    def is_valid_data(self, data):
        if not data['content'] and not data['code_blocks']:
            logging.warning(f'Empty content detected at {data["url"]}')
            return False

        content_hash = hashlib.md5((data['content'] + ''.join([block['code'] for block in data['code_blocks']])).encode()).hexdigest()
        if content_hash in self.content_hashes:
            logging.info(f'Duplicate content found at {data["url"]}')
            return False
        self.content_hashes.add(content_hash)
        return True

    async def crawl_links(self, response):
        links = await self.driver.find_elements(By.TAG_NAME, 'a')
        for link in links:
            href = await link.get_attribute('href')
            if href and href.startswith(self.base_url) and 'sidebar' not in await link.get_attribute('class'):
                yield scrapy.Request(url=href, callback=self.parse)
                self.scraped_links += 1
                self.pbar.update(1)
                scraped_urls_logger.info(href)

    async def retry_scraping(self, response):
        for i in range(2):
            try:
                await self.driver.get(response.url)
                await WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, 'body'))
                )
                print(f"\033[93m\033[1mRetrying scrape of {response.url} (Attempt {i+2}/3)\033[0m")
                self.pbar.set_description(f"Scraping {response.url}")
                await self.parse(response)
                break
            except TimeoutException:
                logging.warning(f'Timeout loading {response.url} on retry {i+2}')
            except Exception as e:
                logging.error(f'Error scraping {response.url} on retry {i+2}: {str(e)}')
                break

    async def parse_github(self, response):
        if response.url in self.visited_urls:
            return

        self.visited_urls.add(response.url)
        g = Github('your_github_access_token')
        
        try:
            repo = await g.get_repo(response.url.split('/')[-1])
        except Exception as e:
            logging.error(f'Error getting GitHub repository: {response.url}, Error: {str(e)}')
            return

        async for content_file in repo.get_contents(""):
            if content_file.type == 'file':
                try:
                    file_content = await (await repo.get_contents(content_file.path)).decoded_content.decode('utf-8')
                except Exception as e:
                    logging.error(f'Error getting file content: {content_file.path}, Error: {str(e)}')
                    continue

                code_blocks = extract_code_blocks(file_content)

                for code_block in code_blocks:
                    language = detect_code_language(code_block)
                    tokens = tokenize_code(code_block, language)
                    data = {
                        'url': response.url,
                        'title': repo.name,
                        'content': file_content,
                        'code_blocks': [
                            {
                                'code': code_block,
                                'language': language,
                                'tokens': tokens
                            }
                        ],
                        'source_type': 'github_repo'
                    }
                    await store_data_in_db([data])
                    scraped_urls_logger.info(response.url)

    async def closed(self, reason):
        await self.driver.quit()
        logging.info('Spider closed')
        self.pbar.close()

class ImprovedCrawlerProcess(CrawlerProcess):
    def __init__(self, settings):
        super().__init__(settings)
        self.crawlers = set()
        self.stopping = False

    async def crawl(self, crawler_or_spidercls, *args, **kwargs):
        crawler = self.create_crawler(crawler_or_spidercls)
        self.crawlers.add(crawler)
        await crawler.crawl(*args, **kwargs)
        self.crawlers.remove(crawler)

    async def stop(self):
        self.stopping = True
        await asyncio.gather(*[c.stop() for c in self.crawlers])

def check_resource_limits():
    process = psutil.Process()
    memory_usage = process.memory_info().rss / 1024 / 1024  # Memory in MB
    cpu_usage = process.cpu_percent()

    if memory_usage > MEMORY_LIMIT:
        logging.warning(f"Memory limit exceeded ({memory_usage:.2f} MB > {MEMORY_LIMIT} MB)")
        # Take action (e.g., pause, exit)

    if cpu_usage > CPU_LIMIT:
        logging.warning(f"CPU limit exceeded ({cpu_usage:.2f}% > {CPU_LIMIT}%)")
        # Take action (e.g., pause, exit)

    time.sleep(1)  # Check resource usage every second

async def main():
    process = ImprovedCrawlerProcess(get_project_settings())
    await process.crawl(WebsiteSpider)
    
    try:
        while not process.stopping:
            if keyboard.is_pressed('q'):
                print("Stopping scraper...")
                await process.stop()
                break
            # Call resource limit check function
            check_resource_limits()
            await asyncio.sleep(0.1)
    except Exception as e:
        logging.error(f"Error in main loop: {str(e)}")
    finally:
        await process.stop()

if __name__ == "__main__":
    asyncio.run(main())