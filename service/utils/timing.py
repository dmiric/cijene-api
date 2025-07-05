import time
from functools import wraps
import logging
import inspect # Import inspect

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def debug_print(message):
    """
    A simple debug print function that uses the logger.
    """
    logger.debug(message)

def timing_decorator(func):
    """
    A decorator that works with both regular async functions and
    async generator functions.
    """
    if inspect.isasyncgenfunction(func):
        @wraps(func)
        async def wrapper_async_gen(*args, **kwargs):
            start_time = time.perf_counter()
            logger.info(f"Streaming call to {func.__name__}...")
            try:
                gen = func(*args, **kwargs)
                async for item in gen:
                    yield item
            finally:
                end_time = time.perf_counter()
                logger.info(
                    f"Stream {func.__name__} finished. "
                    f"Total duration: {end_time - start_time:.4f}s"
                )
        return wrapper_async_gen
    else:
        @wraps(func)
        async def wrapper_async_func(*args, **kwargs):
            start_time = time.perf_counter()
            logger.info(f"Calling {func.__name__}...")
            result = await func(*args, **kwargs)
            end_time = time.perf_counter()
            logger.info(
                f"{func.__name__} finished. "
                f"Total duration: {end_time - start_time:.4f}s"
            )
            return result
        return wrapper_async_func
