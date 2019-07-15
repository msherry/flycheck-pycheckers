#!/usr/bin/env python
"""A hacked up version of the multiple-Python checkers script from EmacsWiki.

Original work taken from http://www.emacswiki.org/emacs/PythonMode, author
unknown.

Further extended by Jason Kirtland <jek@discorporate.us> under the Creative
Commons Share Alike 1.0 license:
http://creativecommons.org/licenses/sa/1.0/

Later improvements by Marc Sherry <msherry@gmail.com>
"""

from __future__ import absolute_import, division, print_function

import os
import re
import sys
import time
from argparse import ArgumentParser, ArgumentTypeError
from csv import DictReader
from distutils.version import LooseVersion
from functools import partial
import shlex
from subprocess import PIPE, Popen, call

# TODO: Ignore the type of conditional imports until
# https://github.com/python/mypy/issues/1107 is fixed
try:
    from StringIO import StringIO  # type: ignore
except ImportError:
    from io import StringIO  # type: ignore
try:
    from configparser import ConfigParser  # type: ignore
except ImportError:
    from ConfigParser import SafeConfigParser as ConfigParser  # type: ignore

try:
    # pylint: disable=unused-import, ungrouped-imports
    from argparse import Namespace
    from typing import (
        Any, Dict, List, IO, Iterable, Optional, Set, Tuple, Union)
except ImportError:
    pass

CONFIG_FILE_NAME = '.pycheckers'

# Checkers to run by default, when no --checkers options are supplied.
default_checkers = 'pylint,mypy2,mypy3'


class FatalException(Exception):
    def __init__(self, msg, filename):
        self.msg = msg
        self.filename = filename
        super(FatalException, self).__init__()

    def __str__(self):
        return 'ERROR :pycheckers:{msg} at {filename} line 1.'.format(
            msg=self.msg, filename=self.filename)


def is_true(v):
    # type: (str) -> bool
    return v.lower() in {'yes', 'true', 't', 'y', 'on', '1'}


def is_false(v):
    # type: (str) -> bool
    return v.lower() in {'no', 'false', 'f', 'n', 'off', '0'}


def str2bool(v):
    # type: (str) -> bool
    if is_true(v):
        return True
    if is_false(v):
        return False
    raise ArgumentTypeError('Boolean value expected.')


def croak(msgs, filename):
    # type: (Tuple[str], str) -> None
    for m in msgs:
        print('ERROR :pycheckers:{} at {} line 1.'.format(m.strip(), filename), file=sys.stderr)
    sys.exit(1)


class LintRunner(object):
    """Base class provides common functionality to run python code checkers."""

    out_fmt = ("%(level)s %(error_type)s%(error_number)s:"
               "%(description)s at %(filename)s line %(line_number)s.")
    out_fmt_w_col = (
        "%(level)s %(error_type)s%(error_number)s:"
        "%(description)s at %(filename)s line %(line_number)s,"
        "%(column_number)s.")

    output_template = dict.fromkeys(
        ('level', 'error_type', 'error_number', 'description',
         'filename', 'line_number', 'column_number'), '')

    output_matcher = re.compile(r'')

    command = ''

    version_args = ('--version',)

    version_matcher = re.compile(r'')

    def __init__(self, ignore_codes, enable_codes, options):
        # type: (Tuple[str], Tuple[str], Namespace) -> None
        self._ignore_codes = set(ignore_codes) if ignore_codes is not None else None
        self.enable_codes = set(enable_codes) if enable_codes is not None else None
        self.options = options

        # The path to the file being checked
        self._filepath = None             # type: Optional[str]
        # The root directory of the current project
        self._project_root = None         # type: Optional[str]
        # The version of the checker, if available
        self._version = None              # type: Optional[LooseVersion]
        # Any debugging output
        self._debug_lines = []            # type: List[str]

    @property
    def ignore_codes(self):
        # type: () -> Optional[Set[str]]
        """Return a list of error codes to ignore, appropriately scoped to the current linter.

        1. Check for {linter}_ignore_codes, and use that if found
        2. (experimental) Check for filters on the codes, and apply them as necessary

        Some linters (like Bandit) raise an error if they are passed an error
        code they do not know about. We allow optionally scoping codes to a set
        of linters in config files, like so:

        [DEFAULT]
        extra_ignore_codes = (C0122:-bandit),(B101:bandit)
        """
        if self._ignore_codes is None:
            return None

        # Check for linter-specific ignore code settings first, and just use those if found.
        ignore_codes_option = '{}_ignore_codes'.format(self.name)
        if hasattr(self.options, ignore_codes_option):
            return set(getattr(self.options, ignore_codes_option).split(','))

        # TODO: this is experimental and may disappear. No one uses it yet, so
        # it's at your own risk.
        positive_matches = set([self.name, self.command, '+' + self.name, '+' + self.command])
        negative_matches = set(['-' + self.name, '-' + self.command])

        ret = set()
        for code_spec in self._ignore_codes:
            if not code_spec.startswith('('):
                # Code is applicable to all checkers of concern
                ret.add(code_spec)
                continue
            # Code is restricted somehow
            code, linters_str = code_spec.split(':')
            linters = linters_str.split(',')
            for linter in linters:
                if linter in positive_matches:
                    ret.add(code)
                    break
                elif linter in negative_matches:
                    break

        # HACK: until we have a better way to handle this, ensure we have a
        # list of acceptable codes per checker so the checker doesn't crash
        # (looking at you, bandit).
        CHECKER_ACCEPTABLE_CODES = {
            'bandit': set(['B101', 'B102', 'B103', 'B104', 'B105', 'B106', 'B107', 'B108',
                           'B109', 'B110', 'B111', 'B112', 'B201', 'B301', 'B302', 'B303',
                           'B304', 'B305', 'B306', 'B307', 'B308', 'B309', 'B310', 'B311',
                           'B312', 'B313', 'B314', 'B315', 'B316', 'B317', 'B318', 'B319',
                           'B320', 'B321', 'B322', 'B401', 'B402', 'B403', 'B404', 'B405',
                           'B406', 'B407', 'B408', 'B409', 'B410', 'B411', 'B412', 'B501',
                           'B502', 'B503', 'B504', 'B505', 'B506', 'B601', 'B602', 'B603',
                           'B604', 'B605', 'B606', 'B607', 'B608', 'B609', 'B701', 'B702', ]),
        }

        if self.name in CHECKER_ACCEPTABLE_CODES:
            ret = set([r for r in ret if r in CHECKER_ACCEPTABLE_CODES[self.name]])
        return ret

    @property
    def name(self):
        # type: () -> str
        """The linter's name, which is usually the same as the command.

        They may be different if there are multiple versions run with
        flags -- e.g. the MyPy2Runner's name may be 'mypy2', even though
        the command is just 'mypy'.
        """
        return self.command

    @property
    def version(self):
        # type: () -> LooseVersion
        """The version of the current checker."""
        if not self._version:
            self._version = LooseVersion(self._get_version() or '0')
            assert self._version  # make mypy happy
        return self._version

    def get_run_flags(self, _filepath):
        # type: (str) -> Iterable[str]
        """Called to build up the list of command-line arguments to pass to the checker."""
        return ()

    def get_env_vars(self):
        # type: () -> Dict[str, str]
        """Called to return any environment variables that should be set for the checker."""
        return {}

    def get_filepath(self, filepath):
        # type: (str) -> str
        """Called to manipulate the path to the file being checked, for checkers that need it."""
        return filepath

    def find_project_root(self, filepath):
        # type: (str) -> str
        """Returns the root of the project that filepath belongs to.

        Attempts to cache lookups to avoid doing extra work."""
        if not self._project_root:
            self._project_root = self._find_project_root(filepath, self.options.venv_root)
        return self._project_root

    def _find_project_root(self, source_file, venv_root):
        # type: (str, str) -> str
        """Find the root directory of the current project.

        1. Walk up the directory tree looking for a VCS directory.
        2. Failing that, find a virtualenv that matches a part of the
               directory, and choose that as the root.
        3. Otherwise, just use the local directory.
        """
        # Case 1
        vcs_root, _vcs_name = find_vcs_root(source_file)
        if vcs_root:
            return vcs_root

        # Case 2
        project_dir, _venv_path = guess_virtualenv(source_file, venv_root)
        if project_dir:
            return project_dir

        # Case 3
        return os.path.dirname(source_file)

    def find_file_in_project_root(self, filename):
        # type: (str) -> Optional[str]
        """Check if `filename` (generally a config file) exists in project
        root, and return the full path if so. Otherwise, return None.
        """
        if not self._filepath:            # This should be set by now
            raise ValueError("self._filepath not set, can't determine project root")
        project_root = self.find_project_root(self._filepath)

        file_path = os.path.join(project_root, filename)
        if os.path.exists(file_path):
            return file_path
        return None

    def find_config_file(self, option_name, config_file_names):
        # type: (str, List[str]) -> Optional[str]
        """Attempt to find a config file -- either specified via the
        given option_name, or by looking in the project root for a given
        default filename. Return a path to the file if present, otherwise
        None.
        """
        config_file = getattr(self.options, option_name, None)
        if config_file:
            if not os.path.exists(config_file):
                raise FatalException(
                    "Can't find config file %s for checker %s" % (config_file, self.name),
                    self._filepath)
        else:
            # Attempt to find one of the `config_file_names` in the project root
            for config_file_name in config_file_names:
                config_file = self.find_file_in_project_root(config_file_name)
                if config_file:
                    break
        return config_file

    def user_defined_command_line(self, filepath):
        # type: (str) -> Optional[List[str]]
        """Allow users to define their own command-lines for checkers.

        E.g. if there is a company-provided script to run mypy, allow users to
        use that instead of the mypy executable directly.
        """
        parts = None
        command_line_option_name = '{}_command'.format(self.name)
        if hasattr(self.options, command_line_option_name):
            parts = shlex.split(getattr(self.options, command_line_option_name))

        if not parts:
            return parts

        substitutions = {
            '%f': filepath,
        }
        def map_substitution(part):
            # type: (str) -> str
            return substitutions.get(part, part)

        return [map_substitution(part) for part in parts]

    def construct_args(self, filepath):
        # type: (str) -> List[str]
        """Construct the argument list for the parser, suitable for passing to Popen."""

        args = self.user_defined_command_line(filepath)
        if args:
            return args

        # `env` to use a virtualenv, if found
        args = ['/usr/bin/env', self.command]
        # Get checker arguments
        args.extend(self.get_run_flags(filepath))
        # Get a checker-specific filename, if necessary
        args.append(self.get_filepath(filepath))
        return args

    def construct_version_args(self):
        # type: () -> List[str]
        """Construct the argument list for finding the parser's version, suitable for passing to Popen."""

        # `env` to use a virtualenv, if found
        args = ['/usr/bin/env', self.command]
        # Get checker arguments
        args.extend(self.version_args)
        return args

    def process_output(self, line):
        # type: (str) -> Optional[Dict[str, str]]
        """Use the matcher to extract fields from the line.

        self.output_matcher can be a function, or a regex that yields named matches."""
        if callable(self.output_matcher):
            return self.output_matcher(line)
        m = self.output_matcher.match(line)
        return m.groupdict() if m else None

    def fixup_data(self, _line, data, _filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]
        """Called to perform any optional cleanups of the parsed data."""
        return data

    def process_returncode(self, _returncode):
        # type: (int) -> bool
        """Return True if the checker's returncode indicates successful check, False otherwise"""
        return True

    def _process_streams(self, filepath, *streams):
        # type: (str, *List[str]) -> Tuple[int, List[str]]
        """This runs over both stdout and stderr, counting errors/warnings."""
        if not streams:
            raise ValueError('No streams passed to _process_streams')
        errors_or_warnings = 0
        out_lines = []
        for stream in streams:
            for line in stream:
                match = self.process_output(line)
                if match:
                    tokens = dict(self.output_template)
                    # Return None from fixup_data to ignore this error
                    fixed_up = self.fixup_data(line, match, filepath)
                    if fixed_up:
                        # Prepend the command name to the description (if
                        # present) so we know which checker threw which error
                        if 'description' in fixed_up:
                            fixed_up['description'] = '%s: %s' % (
                                self.name, fixed_up['description'])
                        tokens.update(fixed_up)
                        template = (
                            self.out_fmt_w_col if fixed_up.get('column_number')
                            else self.out_fmt)
                        out_lines.append(template % tokens)
                        errors_or_warnings += 1
        return errors_or_warnings, out_lines

    def _executable_exists(self):
        # type: () -> bool
        # https://stackoverflow.com/a/6569511/52550
        args = ['/usr/bin/env', 'which', self.command]
        try:
            process = Popen(args, stdout=PIPE, stderr=PIPE)
        except Exception as e:                   # pylint: disable=broad-except
            print(e)
            return False
        exec_path, _err = process.communicate()

        args = ['[', '-x', exec_path.strip(), ']']
        retcode = call(args)
        return retcode == 0

    def run(self, filepath):
        # type: (str) -> Tuple[int, List[str]]
        """The main entry point to a LintRunner.

        Accepts a path to a file to be checked with the given linter,
        and returns a tuple containing the count of error/warning lines,
        and a list of said lines.
        """
        st = time.time()
        if not self._executable_exists():
            # Return a parseable error message so the normal parsing mechanism
            # can display it
            return 1, [
                ('ERROR : {}:Checker not found on PATH, '
                 'unable to check at {} line 1.'.format(
                     self.command, filepath))]

        # Save the path to the file being checked so we don't have to pass it everywhere.
        # TODO: This means we're carrying state around, double-check that we're ok with this.
        self._filepath = filepath

        args = self.construct_args(filepath)

        try:
            process = Popen(
                args, stdout=PIPE, stderr=PIPE, universal_newlines=True,
                env=dict(os.environ, **self.get_env_vars()))
        except Exception as e:                   # pylint: disable=broad-except
            print(e, args)
            return 1, [str(e)]

        out, err = process.communicate()
        process.wait()
        errors_or_warnings, out_lines = self._process_streams(
            filepath, out.splitlines(), err.splitlines())

        if not self.process_returncode(process.returncode):
            errors_or_warnings += 1
            out_lines += [
                ('WARNING : {}:Checker indicated failure of some kind at {} line 1.'.format(
                    self.command, filepath))]
            if self.options.report_checker_errors_inline:
                for line in err.splitlines():
                    out_lines += ['WARNING : {}:{} at {} line 1.'.format(
                        self.command, line, filepath)]

        et = time.time()
        self.debug('Start: %.2fs  end: %.2fs  duration: %.2fs' % (st, et, (et-st)))

        if self.options.debug:
            debug_output = self._get_debug_output()
            errors_or_warnings += len(debug_output)
            out_lines += ['INFO : {}:{} at {} line 1.'.format(
                self.command, line, filepath) for line in debug_output]

        return errors_or_warnings, out_lines

    def debug(self, line):
        # type: (str) -> None
        """Add a new line for debugging output"""
        self._debug_lines.append(line)

    def _get_debug_output(self):
        # type: () -> List[str]
        return self._debug_lines

    def _get_version(self):
        # type: () -> Optional[str]
        """Run the command with a '-V' flag or similar, parse the output, and
        return a version number as a string.
        """
        args = self.construct_version_args()
        try:
            process = Popen(
                args, stdout=PIPE, stderr=PIPE, universal_newlines=True,
                env=dict(os.environ, **self.get_env_vars()))
        except Exception as e:                   # pylint: disable=broad-except
            print(e, args)
            return None

        out, _err = process.communicate()
        process.wait()
        version = self.version_matcher.match(out)
        return version.groupdict().get('version') if version else None


class PyflakesRunner(LintRunner):
    """Run pyflakes, producing flycheck readable output.

    The raw output looks like:
      tests/test_richtypes.py:4: 'doom' imported but unused
      tests/test_richtypes.py:33: undefined name 'undefined'
    or:
      tests/test_richtypes.py:40: could not compile
             deth
            ^
    """

    command = 'pyflakes'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        r'(?P<line_number>[^:]+):'
        r'(?P<description>.+)$')

    @classmethod
    def fixup_data(cls, _line, data, _filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]
        WARNINGS = [
            'imported but unused',
            'redefinition of unused',
            'assigned to but never used',
            'unable to detect undefined names',
        ]
        # Default to 'ERROR' unless a known warning string is found
        data['level'] = 'ERROR'
        for warn_str in WARNINGS:
            if warn_str in data['description']:
                data['level'] = 'WARNING'
                break
        data['error_type'] = 'PY'
        data['error_number'] = 'F'

        return data


class Flake8Runner(LintRunner):
    """Flake8 has similar output to Pyflakes
    """

    command = 'flake8'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        '(?P<line_number>[^:]+):'
        '(?P<column_number>[^:]+): '
        '(?P<error_type>[WEFCNI])(?P<error_number>[^ ]+) '
        '(?P<description>.+)$')

    version_matcher = re.compile(
        r'(?P<version>[0-9.]+).*'
    )

    @classmethod
    def fixup_data(cls, _line, data, _filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]
        if data['error_type'] in ['E']:
            data['level'] = 'WARNING'
        elif data['error_type'] in ['F']:
            data['level'] = 'ERROR'
        else:
            data['level'] = 'WARNING'

        # Unlike pyflakes, flake8 has an error/warning distinction, but some of
        # them are incorrect. Borrow the correct definitions from the pyflakes
        # runner
        if 'imported but unused' in data['description']:
            data['level'] = 'WARNING'
        elif 'redefinition of unused' in data['description']:
            data['level'] = 'WARNING'
        elif 'assigned to but never used' in data['description']:
            data['level'] = 'WARNING'
        elif 'unable to detect undefined names' in data['description']:
            data['level'] = 'WARNING'

        # Flake8 seems to give the full path in the error output, but we only want the basename
        data['filename'] = os.path.basename(data['filename'])

        return data

    def get_run_flags(self, _filepath):
        # type: (str) -> Iterable[str]
        args = []
        if self.ignore_codes is not None:
            if self.version >= LooseVersion('3.6.0'):
                # This only works with flake8 3.6.0+, and *extends*
                # the values given by a config file.
                args.append('--extend-ignore=' + ','.join(self.ignore_codes))
            else:
                # This *overwrites* any values from a config file.
                # We're explicitly ignoring something, even if that something is
                # nothing (i.e. `--ignore=`, meaning ignore nothing)
                args.append('--ignore=' + ','.join(self.ignore_codes))

        config_file = self.find_config_file(
            'flake8_config_file', ['setup.cfg', 'tox.ini', '.flake8'])
        if config_file:
            args += ['--config', config_file]

        args += [
            # TODO: --select, but additive
            # '-select=' + ','.join(self.enable_codes),
            '--max-line-length', str(self.options.max_line_length),
        ]
        return args


class Pep8Runner(LintRunner):
    """Run pep8.py, producing flycheck readable output.

    The raw output looks like:
      spiders/structs.py:3:80: E501 line too long (80 characters)
      spiders/structs.py:7:1: W291 trailing whitespace
      spiders/structs.py:25:33: W602 deprecated form of raising exception
      spiders/structs.py:51:9: E301 expected 1 blank line, found 0

    """

    command = 'pep8'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        r'(?P<line_number>[^:]+):'
        r'(?P<column_number>[^:]+):'
        r' (?P<error_number>\w+) '
        r'(?P<description>.+)$')

    @classmethod
    def fixup_data(cls, _line, data, _filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]
        data['level'] = 'WARNING'
        return data

    def get_run_flags(self, _filepath):
        # type: (str) -> Iterable[str]
        args = []
        if self.ignore_codes is not None:
            args.append('--ignore=' + ','.join(self.ignore_codes))
        args += [
            '--repeat',
            # TODO: make this additive, not a replacement
            # '--select=' + ','.join(self.enable_codes),
            '--max-line-length', str(self.options.max_line_length),
        ]
        return args


class PylintRunner(LintRunner):
    """ Run pylint, producing flycheck readable output.

    The raw output looks like:
    render.py:49: [C0301] Line too long (82/80)
    render.py:1: [C0111] Missing docstring
    render.py:3: [E0611] No name 'Response' in module 'werkzeug'
    render.py:32: [C0111, render] Missing docstring """

    command = 'pylint'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        r'(?P<line_number>\d+):'
        r'(?P<column_number>\d+):'
        r'\s*\[(?P<error_type>[WECR])(?P<error_number>[^(,\]]+)'
        r'\((?P<symbol>[^)]*)\)'
        r'\s*(?P<context>[^\]]*)\]'
        r'\s*(?P<description>.*)$')

    @classmethod
    def fixup_data(cls, _line, data, _filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]
        if data['error_type'].startswith('E'):
            data['level'] = 'ERROR'
        else:
            data['level'] = 'WARNING'

        if data.get('symbol'):
            data['description'] += '  ("{}")'.format(data['symbol'])

        # Pylint column numbers are off by one
        if data.get('column_number') is not None:
            data['column_number'] = str(int(data['column_number']) + 1)
        return data

    def get_run_flags(self, _filepath):
        # type: (str) -> Iterable[str]
        args = []
        if self.ignore_codes is not None:
            args.append('--disable=' + ','.join(self.ignore_codes))
        args += [
            '--msg-template', ('{path}:{line}:{column}: '
                               '[{msg_id}({symbol})] {msg}'),
            '--reports', 'n',
            # This is additive, not replacing
            '--enable=' + ','.join(self.enable_codes),
            '--dummy-variables-rgx=' + '_.*',
            '--max-line-length', str(self.options.max_line_length),
        ]
        if self.options.pylint_rcfile:
            args.extend(['--rcfile', self.options.pylint_rcfile])
        return args

    def get_env_vars(self):
        # type: () -> Dict[str, str]
        env = {}
        if self.options.pylint_rcfile:
            env['PYLINTRC'] = self.options.pylint_rcfile
        return env

    def process_returncode(self, returncode):
        # type: (int) -> bool
        # https://docs.pylint.org/en/1.6.0/run.html, pylint returns a bit-encoded exit code.
        return not (returncode & 1 or returncode & 32)


class MyPy2Runner(LintRunner):

    command = 'mypy'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        r'(?P<line_number>\d+):'
        r'((?P<column_number>\d+):)?'  # Column number is optional, depending on mypy options
        r' (?P<level>[^:]+):'
        r' (?P<description>.+)$')

    version_matcher = re.compile(
        r'mypy (?P<version>[0-9.]+)'
    )

    _base_flags = [
        '--incremental',
    ]

    def _get_cache_dir(self, project_root):
        # type: (str) -> str
        """Find the appropriate .mypy_cache dir for the given branch.

        We attempt to place the cache directory in the project root,
        under a subdir corresponding to the branch name.
        """
        branch_top = os.path.join(project_root, '.mypy_cache', 'branches')
        # It doesn't make sense to get a branch name unless we actually found a
        # VCS root (i.e. a virtualenv match isn't enough)
        branch = ''                       # type: Optional[str]
        if find_vcs_name(project_root):
            branch = get_vcs_branch_name(project_root)
        if branch:
            cache_dir = os.path.join(branch_top, branch)
        else:
            # Can't figure out current branch, just fake it
            cache_dir = os.path.join(branch_top, 'HEAD')
        return cache_dir

    def get_run_flags(self, filepath):
        # type: (str) -> Iterable[str]
        """Determine which mypy (2 or 3) to run, find the cache dir and config file"""

        flags = self._base_flags
        if self.version < LooseVersion('0.660'):
            # --quick-and-dirty is still available
            flags += ['--quick-and-dirty']

        # TODO: this is a hack, we should clean this up in case the file
        # legitimately contains this string
        original_filepath = filepath.replace('flycheck_', '')

        project_root = self.find_project_root(filepath)
        flags += [
            '--cache-dir={}'.format(self._get_cache_dir(project_root)),
        ]
        if self.name == 'mypy':
            # mypy2 mode
            flags += ['--py2']

        config_file = self.find_config_file('mypy_config_file', ['mypy.ini'])
        if config_file:
            flags += ['--config-file', config_file]

        if self.options.mypy_no_implicit_optional:
            flags += ['--no-implicit-optional']

        # Per Guido's suggestion, use the --shadow-file option to work around
        # https://github.com/msherry/flycheck-pycheckers/issues/2, so we can
        # respect per-file mypy.ini config options
        # TODO: only do this when being run by flycheck?
        flags += ['--shadow-file', filepath, original_filepath]
        return flags

    def fixup_data(self, _line, data, filepath):
        # type: (str, Dict[str, str], str) -> Dict[str, str]

        # Mypy returns lines for files other than the current one -- filter
        # those out. Since we may be using the --shadow-file option, check for
        # the original filename, not the flycheck-munged one
        original_filename = os.path.basename(filepath).replace('flycheck_', '')
        if original_filename not in data['filename']:
            return {}
        data['filename'] = os.path.basename(original_filename)

        data['level'] = data['level'].upper()
        if data['level'] == 'NOTE':
            data['level'] = 'INFO'
        return data

    def get_filepath(self, filepath):
        # type: (str) -> str
        """Mypy's weird shadow option means we have to pass the original filepath, not
        the flycheck-munged one
        """
        return filepath.replace('flycheck_', '')


class MyPy3Runner(MyPy2Runner):

    @property
    def name(self):
        # type: () -> str
        return 'mypy3'


class BanditRunner(LintRunner):

    command = 'bandit'
    got_header = False

    def output_matcher(self, line):  # type: ignore
        # type: (str) -> Optional[Dict[str, str]]
        keys = ['filename', 'test_name', 'test_id', 'issue_severity',
                'issue_confidence', 'issue_text', 'line_number', 'line_range']
        f = StringIO(line)
        reader = DictReader(f, fieldnames=keys)
        res = next(reader)
        if not self.got_header:
            # This line was the CSV header, not a real error
            self.got_header = True
            return None
        if res and res.get('test_id'):
            return {
                'description': res['issue_text'],
                'error_number': res['test_id'],
                'filename': res['filename'],
                'level': 'WARNING',
                'line_number': res['line_number'],
            }
        return None

    def get_run_flags(self, _filepath):
        # type: (str) -> Iterable[str]
        flags = ['-f', 'csv']
        if self.ignore_codes is not None:
            # NOTE: this doesn't work if the code isn't recognized as a bandit
            # code (e.g. pylint errors)
            flags += ['--skip', ','.join(self.ignore_codes)]
        return flags


RUNNERS = {
    'pyflakes': PyflakesRunner,
    'flake8': Flake8Runner,
    'pep8': Pep8Runner,
    'pylint': PylintRunner,
    'mypy2': MyPy2Runner,
    'mypy3': MyPy3Runner,
    'bandit': BanditRunner,
}


def get_options_from_file(file_path):
    # type: (str) -> Dict[str, Any]
    """Parse options from the config file at `file_path` and return them as a dict"""
    parsed_options = {}         # type: Dict[str, Union[str, bool]]

    config = ConfigParser()
    config.read(file_path)
    # [DEFAULT] section
    for key, value in config.defaults().items():
        if is_false(value):
            final_value = False           # type: Union[str, bool]
        elif is_true(value):
            final_value = True
        else:
            final_value = value
        parsed_options[key] = final_value
    # NOTE: removed support for per-file config file sections, as I don't think
    # they were being used.
    return parsed_options


def update_options_locally(options):
    # type: (Namespace) -> Namespace
    """Merge options from files.

    Traverse the project directory until a config file is found or the
    filesystem root is reached. If found, use overrides from config as
    project-specific settings.
    """
    allowed_duplicate_options = {'extra_ignore_codes'}
    set_options = set()         # type: Set[str]

    dir_path = os.path.dirname(os.path.abspath(options.file))
    config_file_path = os.path.join(dir_path, CONFIG_FILE_NAME)
    while True:
        if os.path.exists(config_file_path):
            new_options = get_options_from_file(config_file_path)
            for key, value in new_options.items():
                if key in set_options and key not in allowed_duplicate_options:
                    # Already set this option from a file, don't set it again
                    continue
                set_options.add(key)
                # Special handling for some keys

                # Special case config files to contain the full path - assume
                # the specified path is absolute, or relative to the current
                # .pycheckers file
                if 'config_file' in key:
                    if not os.path.isabs(value):
                        value = os.path.join(os.path.dirname(config_file_path), value)
                # Allow for extending, rather than replacing, ignore codes
                elif key == 'extra_ignore_codes':
                    # Still a comma-separated str
                    value = ','.join([options.ignore_codes, value])
                    key = 'ignore_codes'
                setattr(options, key, value)

            if not options.merge_configs:
                # We don't want to walk further up looking for config files
                break

        # Walk up a directory and try again for another file
        parent = os.path.dirname(dir_path)
        if parent == dir_path:
            break
        dir_path = parent
        config_file_path = os.path.join(dir_path, CONFIG_FILE_NAME)
    return options


def run_one_checker(ignore_codes, enable_codes, options, source_file_path, checker_name):
    # type: (Tuple[str], Tuple[str], Namespace, str, str) -> Tuple[int, List[str]]
    checker_class = RUNNERS[checker_name]
    runner = checker_class(ignore_codes, enable_codes, options)
    errors_or_warnings, out_lines = runner.run(source_file_path)
    return (errors_or_warnings, out_lines)


def find_vcs_name(dir_):
    # type: (str) -> Optional[str]
    """If dir_ is a VCS root, return the name of the VCS, otherwise None"""
    for part in ['.git', '.svn', '.hg', '.cvs', '.jedi']:
        path = os.path.join(dir_, part)
        if os.path.exists(path) and os.path.isdir(path):
            return part[1:]             # return the name of the vcs system
    return None


def find_vcs_root(source_file):
    # type: (str) -> Tuple[Optional[str], Optional[str]]
    """Returns the path to the root and the name of the VCS system, if found"""
    cur_dir = os.path.dirname(source_file)
    while True:
        vcs_name = find_vcs_name(cur_dir)
        if vcs_name:
            return cur_dir, vcs_name
        parent = os.path.dirname(cur_dir)
        if parent == cur_dir:
            break              # Hit the FS root without finding VCS info
        cur_dir = parent
    return None, None


def get_vcs_branch_name(vcs_root):
    # type: (str) -> Optional[str]
    """If under source control and the VCS supports branches, find branch name.
    """
    commands = {
        'git': ['git', 'symbolic-ref', '--short', 'HEAD'],
        'hg': ['hg', 'branch'],
    }
    vcs_name = find_vcs_name(vcs_root)
    if not vcs_name or vcs_name not in commands:
        # Unsupported VCS
        return None

    args = commands[vcs_name]
    p = Popen(
        args, stdout=PIPE, stderr=PIPE, cwd=vcs_root, universal_newlines=True)
    out, _err = p.communicate()
    p.wait()
    out = out.strip()
    return out if out else None


def guess_virtualenv(source_file, venv_root):
    # type: (str, str) -> Tuple[Optional[str], Optional[str]]
    """Given the virtualenvwrapper base directory, attempt to guess the paths to
    the project root and the virtualenv that corresponds to this source file,
    based on the project and virtualenv names.

    The virtualenv name must match the name of one of the containing
    directories.
    """
    full_path = os.path.abspath(source_file)
    dir_components = os.path.dirname(full_path).split(os.sep)
    virtualenv_base = os.path.expanduser(venv_root)
    used_components = [os.sep]
    for component in dir_components:
        if not component:
            continue
        used_components.append(component)
        virtualenv_path = os.path.join(virtualenv_base, component)
        if os.path.exists(virtualenv_path):
            return os.path.join(*used_components), virtualenv_path
    return None, None


def set_path_for_virtualenv(source_file, venv_path, venv_root):
    # type: (str, Optional[str], str) -> None
    """Determine if the current file is part of a package that has a
    virtualenv, and munge paths appropriately"""

    if not venv_path:
        # If the venv path isn't supplied directly, try to guess it
        _project_root, venv_path = guess_virtualenv(source_file, venv_root)
    if venv_path:
        bin_path = os.path.join(venv_path, 'bin')
        os.environ['PATH'] = bin_path + ':' + os.environ['PATH']


def parse_args():
    # type: () -> Namespace

    parser = ArgumentParser()
    parser.add_argument('file', type=str, help='Filename to check')
    parser.add_argument("-c", "--checkers", dest="checkers",
                        default=default_checkers,
                        help="Comma-separated list of checkers")
    parser.add_argument("-i", "--ignore-codes", dest="ignore_codes",
                        help="Comma-separated list of error codes to ignore")
    parser.add_argument("-e", "--enable-codes", dest="enable_codes",
                        default='',
                        help="Comma-separated list of error codes to ignore")
    parser.add_argument('--max-line-length', dest='max_line_length',
                        default=79, action='store',
                        help='Maximum line length')
    parser.add_argument('--no-merge-configs', dest='merge_configs',
                        action='store_false',
                        help=('Whether to ignore config files found at a '
                              'higher directory than this one'))
    parser.add_argument('--multi-thread', type=str2bool, default=True,
                        action='store',
                        help=('Run checkers sequentially, '
                              'rather than simultaneously'))
    parser.add_argument('--venv-root', dest='venv_root',
                        default='~/.virtualenvs', action='store',
                        help=('Location of all Python virtual environments. '
                              'Used with auto-detecting virtual envs created by virtualenvwrapper'))
    parser.add_argument('--venv-path', dest='venv_path',
                        default=None, action='store',
                        help=('The full path to a virtualenv. Used with a directly-created '
                              '(not using virtualenvwrapper) virtualenv.'))
    parser.add_argument('--pylint-rcfile', default=None,
                        dest='pylint_rcfile',
                        help='Location of a config file for pylint')
    parser.add_argument('--mypy-config-file', default=None,
                        dest='mypy_config_file',
                        help='Location of a config file for mypy')
    parser.add_argument('--flake8-config-file', default=None,
                        dest='flake8_config_file',
                        help='Location of a config file for flake8')
    parser.add_argument('--report-checker-errors-inline', type=str2bool, default=True,
                        action='store',
                        help=("Whether to fake failing checkers's STDERR as a reported "
                              "error for easier display."))

    parser.add_argument('--mypy-no-implicit-optional', type=str2bool, default=False,
                        action='store')

    parser.add_argument('--debug', action='store_true',
                        help=('Enable output to help debug pycheckers itself'))

    return parser.parse_args()


def main():
    # transparently add a virtualenv to the path when launched with a venv'd
    # python. We can sometimes count on emacs to launch us with the correct
    # python, but we need to handle being run manually, or with emacs in a
    # confused state.
    os.environ['PATH'] = (os.path.dirname(sys.executable) + ':' +
                          os.environ['PATH'])

    options = parse_args()

    source_file_path = options.file
    if not os.path.exists(source_file_path):
        raise RuntimeError("Can't find source file %s" % source_file_path)

    options = update_options_locally(options)

    checkers = options.checkers
    ignore_codes = (tuple(c.strip() for c in options.ignore_codes.split(",") if c)
                    if options.ignore_codes is not None else None)
    enable_codes = tuple(c.strip() for c in options.enable_codes.split(",") if c)
    set_path_for_virtualenv(source_file_path, options.venv_path, options.venv_root)

    checker_names = [checker.strip() for checker in checkers.split(',')]
    try:
        [RUNNERS[checker_name] for checker_name in checker_names]
    except KeyError:
        croak(("Unknown checker {}".format(checker_name),  # pylint: disable=used-before-assignment
               "Expected one of %s" % ', '.join(RUNNERS.keys())),
              filename=options.file)

    if options.multi_thread:
        from multiprocessing import Pool, cpu_count
        p = Pool(cpu_count() + 1)

        func = partial(
            run_one_checker, ignore_codes, enable_codes, options, source_file_path)

        outputs = p.map(func, checker_names, chunksize=1)
        p.close()
        p.join()
        counts, out_lines_list = zip(*outputs)
        errors_or_warnings = sum(counts)
    else:
        errors_or_warnings = 0
        out_lines_list = []
        for checker_name in checker_names:
            e_or_w, o_l = run_one_checker(
                ignore_codes, enable_codes, options, source_file_path, checker_name)
            errors_or_warnings += e_or_w
            out_lines_list.append(o_l)

    for out_lines in out_lines_list:
        for line in out_lines:
            print(line)

    sys.exit(errors_or_warnings > 0)


if __name__ == '__main__':
    main()
