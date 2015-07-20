__author__ = "Martin Blais <blais@furius.ca>"

import textwrap

from beancount import loader
from beancount.parser import cmptest


class TestExampleExcludeTag(cmptest.TestCase):

    @loader.loaddoc
    def test_exclude_tag(self, entries, errors, __):
        """
            plugin "beancount.plugins.exclude_tag"

            2011-01-01 open Expenses:Restaurant
            2011-01-01 open Assets:Cash

            2011-05-17 * "This transaction should be included"
              Expenses:Restaurant   50.02 USD
              Assets:Cash

            2011-05-17 * "This transaction should be excluded" #virtual
              Expenses:Restaurant   11.11 USD
              Assets:Cash
        """
        self.assertFalse(errors)
        self.assertEqualEntries("""

            2011-01-01 open Expenses:Restaurant
            2011-01-01 open Assets:Cash

            2011-05-17 * "This transaction should be included"
              Expenses:Restaurant   50.02 USD
              Assets:Cash          -50.02 USD

        """, entries)
