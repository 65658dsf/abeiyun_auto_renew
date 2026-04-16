import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.microsoft import EdgeChromiumDriverManager

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
LOG_FILE = BASE_DIR / "abeiyun_auto_renew.log"
IMAGE_FILE = BASE_DIR / "ag.png"
LOGIN_URL = "https://www.abeiyun.com/login/"
FREE_SERVER_URL = "https://www.abeiyun.com/control/#/freeServerList"
POST_URL = "https://blog.csdn.net/qwdasfweq/article/details/142734456?spm=1001.2014.3001.5501"
MAX_RETRY = 3
WAIT_SECONDS = 20


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def kill_stale_processes() -> None:
    targets = {"msedge.exe", "msedgedriver.exe"}
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in targets:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def load_users() -> dict:
    if not USERS_FILE.exists():
        raise FileNotFoundError(f"未找到账号配置文件: {USERS_FILE}")
    with USERS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("users.json 格式错误，根节点应为对象")
    return data


def save_users(users: dict) -> None:
    with USERS_FILE.open("w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=4)


def parse_next_submit_time(text: str) -> Optional[str]:
    match = re.search(r"请在(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})后提交", text)
    if match:
        return match.group(1)
    return None


def should_skip_until(info: dict) -> bool:
    next_submit_at = str(info.get("next_submit_at", "")).strip()
    if not next_submit_at:
        return False
    try:
        return datetime.now() < datetime.strptime(next_submit_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False


def create_driver() -> webdriver.Edge:
    options = webdriver.EdgeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--start-maximized")
    
    service = None
    service = Service()

    try:
        driver = webdriver.Edge(service=service, options=options)
        driver.set_page_load_timeout(60)
        return driver
    except Exception as e:
        logging.error("无法启动 Edge 浏览器！")
        logging.error("原因: %s", e)
        logging.error("请尝试以下解决方案：")
        logging.error("1. 检查网络连接，确保能访问 msedgedriver.azureedge.net")
        logging.error("2. 或者手动下载对应版本的 msedgedriver.exe 放到脚本目录下")
        logging.error("   下载地址: https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/")
        raise


def wait_click(driver: webdriver.Edge, locator: tuple, timeout: int = WAIT_SECONDS):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))


def wait_visible(driver: webdriver.Edge, locator: tuple, timeout: int = WAIT_SECONDS):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def clear_session(driver: webdriver.Edge) -> None:
    driver.switch_to.default_content()
    driver.get(LOGIN_URL)
    time.sleep(1)
    driver.delete_all_cookies()
    driver.execute_script("window.localStorage.clear();")
    driver.execute_script("window.sessionStorage.clear();")


def login(driver: webdriver.Edge, username: str, password: str) -> None:
    driver.get(LOGIN_URL)
    user_input = wait_visible(driver, (By.ID, "userName"))
    pwd_input = wait_visible(driver, (By.ID, "passwordInput"))
    submit_btn = wait_click(driver, (By.ID, "loginSubmit"))
    user_input.clear()
    user_input.send_keys(username)
    pwd_input.clear()
    pwd_input.send_keys(password)
    submit_btn.click()
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: "login" not in d.current_url.lower() or "control" in d.current_url.lower()
    )


def switch_to_frame_containing(driver: webdriver.Edge, finder: Callable[[], Optional[object]]) -> Optional[object]:
    driver.switch_to.default_content()
    target = finder()
    if target:
        return target
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for frame in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            target = finder()
            if target:
                return target
            child_frames = driver.find_elements(By.TAG_NAME, "iframe")
            for child in child_frames:
                driver.switch_to.frame(child)
                target = finder()
                if target:
                    return target
                driver.switch_to.parent_frame()
        except WebDriverException:
            driver.switch_to.default_content()
            continue
    driver.switch_to.default_content()
    return None


def click_free_delay(driver: webdriver.Edge) -> None:
    driver.get(FREE_SERVER_URL)
    time.sleep(3)

    def find_delay_button():
        candidates = driver.find_elements(By.XPATH, "//button[contains(.,'免费延期') or contains(.,'延期')]")
        for btn in candidates:
            if btn.is_displayed() and btn.is_enabled():
                return btn
        candidates = driver.find_elements(By.XPATH, "//*[contains(text(),'免费延期') or contains(text(),'延期')]")
        for item in candidates:
            if item.is_displayed():
                return item
        return None

    delay_btn = WebDriverWait(driver, WAIT_SECONDS).until(lambda d: switch_to_frame_containing(d, find_delay_button))
    delay_btn.click()
    time.sleep(2)


def fill_text_like_input(driver: webdriver.Edge, value: str) -> None:
    locators = [
        (By.CSS_SELECTOR, "input[placeholder*='发帖']"),
        (By.CSS_SELECTOR, "textarea[placeholder*='发帖']"),
        (By.CSS_SELECTOR, "input[placeholder*='地址']"),
        (By.CSS_SELECTOR, "textarea[placeholder*='地址']"),
        (By.XPATH, "//input[contains(@name,'url') or contains(@id,'url')]"),
        (By.XPATH, "//textarea[contains(@name,'url') or contains(@id,'url')]"),
        (By.XPATH, "//input[contains(@name,'link') or contains(@id,'link')]"),
        (By.XPATH, "//textarea[contains(@name,'link') or contains(@id,'link')]"),
    ]
    for locator in locators:
        elems = driver.find_elements(*locator)
        for elem in elems:
            if elem.is_displayed():
                elem.clear()
                elem.send_keys(value)
                return
    editable = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea")
    for elem in editable:
        if elem.is_displayed() and elem.is_enabled():
            elem.clear()
            elem.send_keys(value)
            return
    raise TimeoutException("未找到发帖地址输入框")


def upload_image(driver: webdriver.Edge) -> None:
    if not IMAGE_FILE.exists():
        raise FileNotFoundError(f"未找到图片文件: {IMAGE_FILE}")
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
    for file_input in inputs:
        if file_input.is_enabled():
            file_input.send_keys(str(IMAGE_FILE))
            return
    raise TimeoutException("未找到上传控件 input[type=file]")


def click_submit(driver: webdriver.Edge) -> None:
    selectors = [
        (By.XPATH, "//button[contains(.,'提交')]"),
        (By.XPATH, "//a[contains(.,'提交')]"),
        (By.XPATH, "//*[contains(text(),'提交')]"),
    ]
    for locator in selectors:
        elems = driver.find_elements(*locator)
        for elem in elems:
            if elem.is_displayed() and elem.is_enabled():
                elem.click()
                return
    raise TimeoutException("未找到提交按钮")


def wait_submit_status(driver: webdriver.Edge) -> str:
    success_keywords = ("延期申请等待审核中", "提交成功", "成功")
    fail_keywords = ("失败", "错误", "异常", "请稍后")
    end_time = time.time() + 20
    while time.time() < end_time:
        page_text = driver.page_source
        for word in success_keywords:
            if word in page_text:
                return f"SUCCESS:{word}"
        for word in fail_keywords:
            if word in page_text:
                return f"FAIL:{word}"
        time.sleep(1)
    return "UNKNOWN:未识别到明确状态"


def try_fill_form(driver: webdriver.Edge) -> str:
    # 0. 在尝试寻找表单前，先快速检查一次是否有“审核中”提示或未到提交时间
    try:
        page_text = driver.page_source
        next_submit_at = parse_next_submit_time(page_text)
        if next_submit_at:
            return f"SKIP:未到提交时间:{next_submit_at}"

        def check_reviewing():
            return "延期申请等待审核中" in driver.page_source

        # 尝试切换 frame 并检查
        is_reviewing = switch_to_frame_containing(driver, lambda: True if check_reviewing() else None)
        if is_reviewing:
            return "SUCCESS:延期申请等待审核中_已跳过"

        next_submit_at = parse_next_submit_time(driver.page_source)
        if next_submit_at:
            return f"SKIP:未到提交时间:{next_submit_at}"
    except Exception:
        pass

    def find_form_anchor():
        form_markers = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'发帖') or contains(text(),'延期') or contains(text(),'截图') or contains(text(),'提交')]",
        )
        for marker in form_markers:
            if marker.is_displayed():
                return marker
        return None

    try:
        marker = WebDriverWait(driver, 15).until(lambda d: switch_to_frame_containing(d, find_form_anchor))
        if not marker:
            raise TimeoutException("未找到延期表单区域")
        
        # 检查是否已经是审核中状态或未到提交时间
        try:
            # 检查 marker 本身文本
            if "延期申请等待审核中" in marker.text:
                return "SUCCESS:延期申请等待审核中_已跳过"
            next_submit_at = parse_next_submit_time(marker.text)
            if next_submit_at:
                return f"SKIP:未到提交时间:{next_submit_at}"
            # 检查当前 frame 页面源码
            if "延期申请等待审核中" in driver.page_source:
                return "SUCCESS:延期申请等待审核中_已跳过"
            next_submit_at = parse_next_submit_time(driver.page_source)
            if next_submit_at:
                return f"SKIP:未到提交时间:{next_submit_at}"
            # 专门查找提示元素
            reviewing_tips = driver.find_elements(By.XPATH, "//*[contains(text(),'延期申请等待审核中')]")
            for tip in reviewing_tips:
                if tip.is_displayed():
                    return "SUCCESS:延期申请等待审核中_已跳过"
        except Exception:
            pass

        fill_text_like_input(driver, POST_URL)
        time.sleep(1)
        upload_image(driver)
        time.sleep(1)
        click_submit(driver)
        return wait_submit_status(driver)
    except TimeoutException:
        # 如果找不到表单，最后再检查一次是否因为已经是审核中或未到提交时间
        try:
            if "延期申请等待审核中" in driver.page_source:
                return "SUCCESS:延期申请等待审核中_已跳过"
            next_submit_at = parse_next_submit_time(driver.page_source)
            if next_submit_at:
                return f"SKIP:未到提交时间:{next_submit_at}"
        except Exception:
            pass
        raise


def logout_cleanup(driver: webdriver.Edge) -> None:
    try:
        driver.switch_to.default_content()
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
    except Exception:
        pass
    clear_session(driver)


def process_one_user(driver: webdriver.Edge, username: str, password: str) -> tuple[bool, Optional[str]]:
    for attempt in range(1, MAX_RETRY + 1):
        try:
            logging.info("账号 %s 开始第 %s/%s 次尝试", username, attempt, MAX_RETRY)
            clear_session(driver)
            login(driver, username, password)
            click_free_delay(driver)
            status = try_fill_form(driver)
            logging.info("账号 %s 提交状态: %s", username, status)
            if status.startswith("SUCCESS"):
                return True, None
            if status.startswith("SKIP:未到提交时间:"):
                return True, status.split(":", 2)[2]
        except Exception as e:
            logging.error("账号 %s 第 %s 次失败: %s", username, attempt, e)
        finally:
            try:
                logout_cleanup(driver)
            except Exception:
                pass
    return False, None


def main() -> None:
    setup_logging()
    logging.info("阿贝云自动续费任务启动")
    # kill_stale_processes()  # 禁用进程清理，避免影响用户其他浏览器
    users = load_users()
    driver = create_driver()
    try:
        for phone, info in users.items():
            password = str(info.get("password", "")).strip()
            if not password:
                logging.warning("账号 %s 未配置密码，已跳过", phone)
                continue
            if should_skip_until(info):
                logging.info("账号 %s 未到下次提交时间 %s，已跳过", phone, info.get("next_submit_at"))
                continue
            ok, next_submit_at = process_one_user(driver, phone, password)
            if ok:
                if next_submit_at:
                    users[phone]["next_submit_at"] = next_submit_at
                    save_users(users)
                    logging.info("账号 %s 未到提交时间，已记录下次可提交时间 %s", phone, next_submit_at)
                else:
                    users[phone].pop("next_submit_at", None)
                    users[phone]["lastsign"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    save_users(users)
                    logging.info("账号 %s 处理成功", phone)
            else:
                logging.error("账号 %s 处理失败，已达到最大重试次数", phone)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    logging.info("阿贝云自动续费任务结束")


if __name__ == "__main__":
    main()
