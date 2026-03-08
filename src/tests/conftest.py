"""
src/tests/conftest.py

pytest fixtures that set up and tear down the test environment.

FIXTURE LIFECYCLE:
───────────────────
session scope  → Created once for the whole test run
module scope   → Created once per test file
function scope → Created/destroyed for each individual test

We use "session" scope for the Appium driver so we don't
reconnect to the device for every test (slow and unnecessary).
"""

import pytest
from loguru import logger
from appium.webdriver.webdriver import WebDriver

from src.utils.appium_driver import create_driver, quit_driver
from src.llm.claude_client import ClaudeClient
from src.agents.browser_agent import BrowserAgent
from src.agents.content_reader_agent import ContentReaderAgent
from src.config.settings import appium_config, llm_config, test_config


def pytest_configure(config):
    """Called after command line options have been parsed."""
    logger.info("=" * 60)
    logger.info("Test Session Configuration")
    logger.info(f"  Target URL:    {test_config.target_url}")
    logger.info(f"  Search query:  {test_config.search_query}")
    logger.info(f"  Appium server: {appium_config.server_url}")
    logger.info(f"  Device:        {appium_config.device_name}")
    logger.info("=" * 60)


@pytest.fixture(scope="session")
def claude_client() -> ClaudeClient:
    """
    Create a single Claude API client for the entire test session.

    scope="session" means this is created once and shared across all tests.
    """
    logger.info("Initializing Claude LLM client...")
    client = ClaudeClient()
    yield client
    logger.info("Claude client session ended")


@pytest.fixture(scope="session")
def appium_driver() -> WebDriver:
    """
    Create and yield the Appium WebDriver session.

    scope="session" — driver is created once for all tests.
    After all tests complete, the driver is quit.

    Yielding (vs returning) allows cleanup code after yield to run.
    """
    logger.info("Creating Appium driver for test session...")
    driver = create_driver()

    yield driver  # Tests run here

    # Cleanup runs after all tests complete
    logger.info("Tearing down Appium driver...")
    quit_driver(driver)


@pytest.fixture(scope="function")
def browser_agent(appium_driver, claude_client) -> BrowserAgent:
    """
    Create a fresh BrowserAgent for each test function.

    scope="function" means a new agent is created per test,
    but reuses the same driver and claude client.
    """
    return BrowserAgent(driver=appium_driver, claude=claude_client)


@pytest.fixture(scope="function")
def content_reader_agent(appium_driver, claude_client) -> ContentReaderAgent:
    """Create a fresh ContentReaderAgent for each test function."""
    return ContentReaderAgent(driver=appium_driver, claude=claude_client)


@pytest.fixture(scope="session")
def target_url() -> str:
    """The target URL from configuration."""
    return test_config.target_url


@pytest.fixture(scope="session")
def search_query() -> str:
    """The search query from configuration."""
    return test_config.search_query
