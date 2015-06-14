__author__ = "Martin Blais <blais@furius.ca>"

import os
import subprocess
import textwrap
import subprocess
from os import path
from unittest import mock
import re

import lxml.html

from beancount.utils import test_utils
from beancount.utils import file_utils
from beancount.scripts import bake


class TestBakeFunctions(test_utils.TestCase):

    def test_normalize_filename(self):
        # Index files should become index.
        self.assertEqual('/path/to/dir/index.html',
                         bake.normalize_filename('/path/to/dir/'))

        # Files without extensions should become .html
        self.assertEqual('/path/to/file.html',
                         bake.normalize_filename('/path/to/file'))

        # Existing extensions that shouldn't be touched.
        self.assertEqual('/path/to/file.html',
                         bake.normalize_filename('/path/to/file.html'))
        self.assertEqual('/favicon.ico',
                         bake.normalize_filename('/favicon.ico'))
        self.assertEqual('/resources/file.css',
                         bake.normalize_filename('/resources/file.css'))
        self.assertEqual('/resources/file.js',
                         bake.normalize_filename('/resources/file.js'))

        # Other extensions should become .html.
        self.assertEqual('/path/to/file.csv.html',
                         bake.normalize_filename('/path/to/file.csv'))
        self.assertEqual('/path/to/file.png.html',
                         bake.normalize_filename('/path/to/file.png'))
        self.assertEqual('/link/tag.pdf.html',
                         bake.normalize_filename('/link/tag.pdf'))

        # Unless they are in doc or third_party.
        self.assertEqual('/doc/file.csv',
                         bake.normalize_filename('/doc/file.csv'))
        self.assertEqual('/third_party/file.csv',
                         bake.normalize_filename('/third_party/file.csv'))


    test_html = textwrap.dedent("""
      <html>
        <body>
          <a href="/path/parent">parent file</a>
          <a href="/path/">parent dir</a>
          <a href="/path/to/other">sibling file</a>
          <a href="/path/to/">sibling dir</a>
          <a href="/path/to/sub/child">child file</a>
          <a href="/path/to/sub/">child dir</a>
          <img src="/third_party/image.png"/>
        </body>
      </html>
    """)

    expected_html = textwrap.dedent("""
      <html>
        <body>
          <a href="../parent.html">parent file</a>
          <a href="../index.html">parent dir</a>
          <a href="other.html">sibling file</a>
          <a href="index.html">sibling dir</a>
          <a href="sub/child.html">child file</a>
          <a href="sub/index.html">child dir</a>
          <img src="../../third_party/image.png"/>
        </body>
      </html>
    """)

    maxDiff = None

    def test_relativize_links(self):
        html = lxml.html.document_fromstring(self.test_html)
        self.assertLines(
            self.expected_html,
            bake.relativize_links(html, '/path/to/index').decode('utf8'))

    def test_save_scraped_document__file(self):
        html = lxml.html.document_fromstring(self.test_html)
        with test_utils.tempdir() as tmp:
            response = mock.MagicMock()
            response.url = '/path/to/file'
            response.status = 200
            response.read.return_value = self.test_html.encode('utf8')
            response.info().get_content_type.return_value = 'text/html'

            bake.save_scraped_document(tmp, response, html)
            filename = path.join(tmp, 'path/to/file.html')
            self.assertTrue(path.exists(filename))
            self.assertLines(open(filename).read(), self.expected_html)

    def test_save_scraped_document__ignore_directories(self):
        html = lxml.html.document_fromstring(self.test_html)
        with test_utils.tempdir() as tmp:
            response = mock.MagicMock()
            response.url = '/doc/to/'
            response.status = 200
            response.read.return_value = self.test_html.encode('utf8')
            response.info().get_content_type.return_value = 'text/html'

            bake.save_scraped_document(tmp, response, html)
            self.assertEqual([], os.listdir(tmp))

    def test_save_scraped_document__binary_content(self):
        html = lxml.html.document_fromstring(self.test_html)
        with test_utils.tempdir() as tmp:
            response = mock.MagicMock()
            response.url = '/resources/something.png'
            response.status = 200
            response.read.return_value = 'IMAGE!'.encode('utf8')
            response.info().get_content_type.return_value = 'image/png'

            bake.save_scraped_document(tmp, response, html)
            self.assertEqual(b'IMAGE!', open(path.join(tmp, 'resources/something.png'), 'rb').read())


class TestScriptBake(test_utils.TestCase):

    def get_args(self):
        return ['--quiet', '--port', str(test_utils.get_test_port())]

    def test_bake_missing_input(self):
        with test_utils.tempdir() as tmpdir:
            with self.assertRaises(SystemExit):
                output = path.join(tmpdir, 'output')
                filename = path.join(tmpdir, 'does_not_exist.beancount')
                test_utils.run_with_args(bake.main, self.get_args() + [filename, output])

    @test_utils.docfile
    def test_bake_output_collision(self, filename):
        """
        2013-01-01 open Expenses:Restaurant
        2013-01-01 open Assets:Cash

        2014-03-02 * "Some basic transaction"
          Expenses:Restaurant   50.02 USD
          Assets:Cash
        """
        with test_utils.tempdir() as tmpdir:
            with self.assertRaises(SystemExit):
                output = path.join(tmpdir, 'output')
                os.mkdir(output)
                test_utils.run_with_args(bake.main, self.get_args() + [filename, output])

        with test_utils.tempdir() as tmpdir:
            with self.assertRaises(SystemExit):
                output = path.join(tmpdir, 'output.tar.gz')
                os.mkdir(output)
                test_utils.run_with_args(bake.main, self.get_args() + [filename, output])

        with test_utils.tempdir() as tmpdir:
            with self.assertRaises(SystemExit):
                output = path.join(tmpdir, 'output.tar.gz')
                open(output, 'w')
                test_utils.run_with_args(bake.main, self.get_args() + [filename, output])

    @test_utils.docfile
    def test_bake_directory(self, filename):
        """
        2013-01-01 open Expenses:Restaurant
        2013-01-01 open Assets:Cash

        2014-03-02 * "Some basic transaction"
          Expenses:Restaurant   50.02 USD
          Assets:Cash
        """
        with test_utils.tempdir() as tmpdir:
            outdir = path.join(tmpdir, 'output')
            with test_utils.capture() as output:
                test_utils.run_with_args(bake.main, self.get_args() + [filename, outdir])
            self.assertTrue(output.getvalue())
            self.assertTrue(path.exists(outdir) and path.isdir(outdir))
            directories = [root for root, _, _ in os.walk(outdir)]
            self.assertGreater(len(directories), 10)

    @test_utils.docfile
    def test_bake_bad_link(self, filename):
        """
        plugin "beancount.ops.auto_accounts"

        2014-03-02 * "Something" ^2015-06-14.something.pdf
          Expenses:Restaurant   1 USD
          Assets:Cash
        """
        with test_utils.tempdir(delete=0) as tmpdir:
            tmpdir = path.join(tmpdir, 'output')
            with test_utils.capture() as output:
                test_utils.run_with_args(bake.main, self.get_args() + [filename, tmpdir])
            self.assertTrue(output.getvalue())
            self.assertFalse(path.exists(
                path.join(tmpdir, 'link/2015-06-14.something.pdf')))
            self.assertTrue(path.exists(
                path.join(tmpdir, 'link/2015-06-14.something.pdf.html')))


class TestScriptArchive(TestScriptBake):

    @test_utils.docfile
    def test_bake_archive__known(self, filename):
        """
        2013-01-01 open Expenses:Restaurant
        2013-01-01 open Assets:Cash

        2014-03-02 * "Some basic transaction"
          Expenses:Restaurant   50.02 USD
          Assets:Cash
        """
        with test_utils.tempdir() as tmpdir:
            for archive_name in ('archive.tar.gz',
                                 'archive.tgz',
                                 'archive.tar.bz2',
                                 'archive.zip'):
                outfile = path.join(tmpdir, archive_name)
                with test_utils.capture():
                    test_utils.run_with_args(bake.main,
                                             self.get_args() + [filename, outfile])
                self.assertFalse(path.exists(file_utils.path_greedy_split(outfile)[0]))
                self.assertTrue(path.exists(outfile) and path.getsize(outfile) > 0)

    @test_utils.docfile
    def test_bake_archive__unknown(self, filename):
        """
        2013-01-01 open Expenses:Restaurant
        2013-01-01 open Assets:Cash

        2014-03-02 * "Some basic transaction"
          Expenses:Restaurant   50.02 USD
          Assets:Cash
        """
        with test_utils.tempdir() as tmpdir:
            for archive_name in ('archive.tar.zip',
                                 'archive.tar.xz'):
                with self.assertRaises(SystemExit):
                    outfile = path.join(tmpdir, archive_name)
                    test_utils.run_with_args(bake.main,
                                             self.get_args() + [filename, outfile])
