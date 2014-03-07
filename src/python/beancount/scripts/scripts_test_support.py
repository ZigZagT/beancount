"""Support utillities for testing scripts.
"""
import textwrap
import unittest
import io
import tempfile
import sys
import contextlib
import functools


def run_with_args(function, args):
    """Run the given function with sys.argv set to argv. The first argument is
    automatically inferred to be where the function object was defined. sys.argv
    is restored after the function is called.

    Args:
      function: A function object to call with no arguments.
      argv: A list of arguments, excluding the script name, to be temporarily
        set on sys.argv.
    """
    saved_argv = sys.argv
    try:
        sys.argv = [function.__module__] + args
        function()
    finally:
        sys.argv = saved_argv


@contextlib.contextmanager
def capture():

    """A context manager that captures what's printed to stdout.

    Yields:
      A StringIO string accumulator.
    """
    sys.saved_stdout = sys.stdout
    oss = sys.stdout = io.StringIO()
    yield oss
    sys.stdout = sys.saved_stdout


def docfile(function):
    """A decorator that write the function's docstring to a temporary file
    and calls the decorated function with the temporary filename.  This is
    useful for writing tests.

    Args:
      function: A function to decorate.
    Returns:
      The decorated function.
    """
    @functools.wraps(function)
    def new_function(self):
        with tempfile.NamedTemporaryFile('w') as f:
            f.write(textwrap.dedent(function.__doc__))
            f.flush()
            return function(self, f.name)
    return new_function


class TestCase(unittest.TestCase):

    def assertLines(self, text1, text2, message=None):
        """Compare the lines of text1 and text2, ignoring whitespace.

        Args:
          text1: A string, the expected text.
          text2: A string, the actual text.
          message: An optional string message in case the assertion fails.
        Raises:
          AssertionError: If the exception fails.
        """
        lines1 = [line.strip() for line in text1.strip().splitlines()]
        lines2 = [line.strip() for line in text2.strip().splitlines()]
        self.assertEqual(lines1, lines2, message)

    @contextlib.contextmanager
    def assertOutput(self, expected_text):
        """Expect text printed to stdout.

        Args:
          expected_text: A string, the text that should have been printed to stdout.
        Raises:
          AssertionError: If the text differs.
        """
        with capture() as oss:
            yield oss
        self.assertLines(textwrap.dedent(expected_text), oss.getvalue())
