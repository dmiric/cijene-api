import pytest

def pytest_addoption(parser):
    """
    Adds a custom command-line option to pytest to specify a chat query.
    """
    parser.addoption(
        "--query", 
        action="store", 
        default=None, 
        help="Specify the initial chat query to test. If not provided, a default query will be used."
    )

@pytest.fixture
def initial_query(request):
    """
    A fixture that retrieves the value of the --query command-line option.
    """
    return request.config.getoption("--query")