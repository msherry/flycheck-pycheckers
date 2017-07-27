#!/usr/bin/env python
"""A hacked up version of the multiple-Python checkers script from EmacsWiki.

Original work taken from http://www.emacswiki.org/emacs/PythonMode, author
unknown.

Further extended by Jason Kirtland <jek@discorporate.us> under the Creative
Commons Share Alike 1.0 license:
http://creativecommons.org/licenses/sa/1.0/

Later improvements by Marc Sherry <msherry@gmail.com>
"""

from argparse import ArgumentParser, ArgumentTypeError
import ConfigParser
from functools import partial
import os
import re
from subprocess import call, Popen, PIPE
import sys

try:
    # pylint: disable=C0412, W0611
    from argparse import Namespace     # noqa: F401
    from typing import (               # noqa: F401
        Dict, List, IO, Optional, Set, Tuple)
except ImportError:
    pass

# Customization #

# Checkers to run by default, when no --checkers options are supplied.
# default_checkers = 'flake8,pylint,mypy2,mypy3'
default_checkers = 'pylint,mypy2,mypy3'

# A list of error codes to ignore for PEP8
default_ignore_codes = [
    # 'E202',          # Whitespace before ']'
    # 'E221',          # Multiple spaces before operator
    # 'E225',          # Missing whitespace around operator
    # 'E231',          # Missing whitespace after ':'
    # 'E241',          # Multiple spaces after ':'
    # 'E261',          # At least two spaces before inline comment
    # 'W291',          # Trailing whitespace
    # 'E301',          # Expected 1 blank line, found 0
    # 'E302',          # Expected 2 blank lines, found 1
    # 'E303',          # Too many blank lines
    # 'E401',          # Multiple imports on one line
    # 'E501',          # Line too long

    # 'E127',          # continuation line over-indented for visual indent
    # 'E128',          # continuation line under-indented for visual indent
    # 'E711',            # comparison to None should be...
    # 'E712',            # comparison to True/False should be ...

    'C0411',           # external import "..." comes before "..."
    'C0413',           # Import "..." should be placed at the top of the module
]

# End of customization #


class LintRunner(object):
    """Base class provides common functionality to run python code checkers."""

    output_format = ("%(level)s %(error_type)s%(error_number)s:"
                     "%(description)s at %(filename)s line %(line_number)s.")
    output_format_w_column = ("%(level)s %(error_type)s%(error_number)s:"
                              "%(description)s at %(filename)s line %(line_number)s,"
                              "%(column_number)s.")

    output_template = dict.fromkeys(
        ('level', 'error_type', 'error_number', 'description',
         'filename', 'line_number', 'column_number'), '')

    output_matcher = re.compile(r'')

    sane_default_ignore_codes = set()      # type: Set[str]

    command = ''

    def __init__(self, ignore_codes=None, use_sane_defaults=True, options=None):
        # type: (Optional[Tuple[str, ...]], bool, Namespace) -> None
        ignore_codes = ignore_codes or ()
        self.ignore_codes = set(ignore_codes)
        if use_sane_defaults:
            self.ignore_codes |= self.sane_default_ignore_codes
        self.options = options

    @property
    def name(self):
        # type: () -> str
        """The linter's name, which is usually the same as the command.

        They may be different if there are multiple versions run with
        flags -- e.g. the MyPy2Runner's name may be 'mypy2', even though
        the command is just 'mypy'.
        """
        return self.command

    def get_run_flags(self, _filename):
        # type: (str) -> Tuple[str, ...]
        return ()

    def fixup_data(self, _line, data):
        # type: (str, Dict[str, str]) -> Optional[Dict[str, str]]
        return data

    def process_output(self, line):
        # type: (str) -> Optional[Dict[str, str]]
        m = self.output_matcher.match(line)
        if m:
            return m.groupdict()
        return None

    def _process_streams(self, *streams):
        # type: (*List[str]) -> Tuple[int, List[str]]
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
                    fixed_up = self.fixup_data(line, match)
                    if fixed_up:
                        # Prepend the command name to the description (if
                        # present) so we know which checker threw which error
                        if 'description' in fixed_up:
                            fixed_up['description'] = '%s: %s' % (
                                self.name, fixed_up['description'])
                        tokens.update(fixed_up)
                        template = (self.output_format_w_column if fixed_up.get('column_number')
                                    else self.output_format)
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
            print e
            return False
        exec_path, _err = process.communicate()

        args = ['[', '-x', exec_path.strip(), ']']
        retcode = call(args)
        return retcode == 0

    def run(self, filename):
        # type: (str) -> Tuple[int, List[str]]

        if not self._executable_exists():
            # Return a parseable error message so the normal parsing mechanism can display it
            return 1, [
                'WARNING : {}:Checker not found on PATH, unable to check at {} line 1.'.format(
                    self.command, filename)]

        # `env` to use a virtualenv, if found
        args = ['/usr/bin/env', self.command]
        args.extend(self.get_run_flags(filename))
        args.append(filename)

        try:
            process = Popen(args, stdout=PIPE, stderr=PIPE)
        except Exception as e:                   # pylint: disable=broad-except
            print e, args
            return 1, [str(e)]

        out, err = process.communicate()
        process.wait()
        errors_or_warnings, out_lines = self._process_streams(
            out.splitlines(), err.splitlines())

        return errors_or_warnings, out_lines


class PyflakesRunner(LintRunner):
    """Run pyflakes, producing flymake readable output.

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
    def fixup_data(cls, _line, data):
        if 'imported but unused' in data['description']:
            data['level'] = 'WARNING'
        elif 'redefinition of unused' in data['description']:
            data['level'] = 'WARNING'
        elif 'assigned to but never used' in data['description']:
            data['level'] = 'WARNING'
        elif 'unable to detect undefined names' in data['description']:
            data['level'] = 'WARNING'
        else:
            data['level'] = 'ERROR'
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
        '(?P<error_type>[WEFCN])(?P<error_number>[^ ]+) '
        '(?P<description>.+)$')

    @classmethod
    def fixup_data(cls, _line, data):
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

        return data

    def get_run_flags(self, _filename):
        return (
            '--ignore=' + ','.join(self.ignore_codes),
            '--max-line-length', str(self.options.max_line_length),
        )


class Pep8Runner(LintRunner):
    """Run pep8.py, producing flymake readable output.

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
    def fixup_data(cls, _line, data):
        data['level'] = 'WARNING'
        return data

    def get_run_flags(self, _filename):
        return (
            '--repeat',
            '--ignore=' + ','.join(self.ignore_codes),
            '--max-line-length', str(self.options.max_line_length),
        )


class PylintRunner(LintRunner):
    """ Run pylint, producing flymake readable output.

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

    sane_default_ignore_codes = {
        "C0103",  # Naming convention
        "C0111",  # Missing Docstring
        "W0142",
        "W0201",  # "Attribute defined outside __init__"
        "W0232",  # No __init__
        "W0403",
        "W0511",
        "E1002",  # Use super on old-style class
        "E1101",
        "E1103",  # Instance of x has no y member
                  # (but some types could not be inferred")
        "R0201",  # Method could be a function
        "R0801",  # Similar lines in * files
        "R0903",  # Too few public methods
        "R0904",  # Too many public methods
        "R0914",  # Too many local variables
    }

    @classmethod
    def fixup_data(cls, _line, data):
        if data['error_type'].startswith('E'):
            data['level'] = 'ERROR'
        else:
            data['level'] = 'WARNING'

        if data.get('symbol'):
            data['description'] += '  ("{}")'.format(data['symbol'])
        return data

    def get_run_flags(self, _filename):
        return (
            '--msg-template', '{path}:{line}:{column}: [{msg_id}({symbol})] {msg}',
            '--reports', 'n',
            '--disable=' + ','.join(self.ignore_codes),
            '--dummy-variables-rgx=' + '_.*',
            '--max-line-length', str(self.options.max_line_length),
            '--rcfile', self.options.pylint_rcfile,
        )


class MyPy2Runner(LintRunner):

    command = 'mypy'

    output_matcher = re.compile(
        r'(?P<filename>[^:]+):'
        r'(?P<line_number>[^:]+):'
        r' (?P<level>[^:]+):'
        r' (?P<description>.+)$')

    _base_flags = (
        '--incremental',
        '--quick-and-dirty',
        '--ignore-missing-imports',
        '--strict-optional',
    )

    def _get_cache_dir(self, filename):
        # type: (str) -> str
        """Find the appropriate .mypy_cache dir for the given branch.

        We attempt to place the cache directory in the project root,
        under a subdir corresponding to the branch name.
        """
        branch_top = os.path.join(
            find_project_root(filename), '.mypy_cache', 'branches')
        branch = get_vcs_branch_name(filename)
        if branch:
            cache_dir = os.path.join(branch_top, branch)
        else:
            # ERROR: can't figure out current branch
            cache_dir = os.path.join(branch_top, 'HEAD')
        return cache_dir

    def get_run_flags(self, filename):
        return (
            '--py2',
            '--cache-dir={}'.format(self._get_cache_dir(filename)),
        ) + self._base_flags

    def fixup_data(self, _line, data):
        data['level'] = data['level'].upper()
        if data['level'] == 'NOTE':
            return None
        return data


class MyPy3Runner(MyPy2Runner):

    @property
    def name(self):
        return 'mypy3'

    def get_run_flags(self, filename):
        return (
            '--cache-dir={}'.format(self._get_cache_dir(filename)),
        ) + self._base_flags


def croak(*msgs):
    for m in msgs:
        print >> sys.stderr, m.strip()
    sys.exit(1)


RUNNERS = {
    'pyflakes': PyflakesRunner,
    'flake8': Flake8Runner,
    'pep8': Pep8Runner,
    'pylint': PylintRunner,
    'mypy2': MyPy2Runner,
    'mypy3': MyPy3Runner,
}


def update_options_from_file(options, config_file_path):
    config = ConfigParser.SafeConfigParser()
    config.read(config_file_path)

    def _is_false(value):
        return value.lower() in {'false', 'f'}

    def _is_true(value):
        return value.lower() in {'true', 't'}

    for key, value in config.defaults().iteritems():
        if _is_false(value):
            value = False
        elif _is_true(value):
            value = True
        setattr(options, key, value)
    for section_name in config.sections():
        if (re.search(section_name, options.file) or
                re.search(section_name, options.file.replace('_flymake', ''))):
            for key, value in config.items(section_name):
                if _is_false(value):
                    value = False
                elif _is_true(value):
                    value = True
                setattr(options, key, value)
    if hasattr(options, 'extra_ignore_codes'):
        extra_ignore_codes = (
            options.extra_ignore_codes.replace(',', ' ').split())
        # Allow for extending, rather than replacing, ignore codes
        options.ignore_codes.extend(extra_ignore_codes)
    return options


def update_options_locally(options):
    # type: (Namespace) -> Namespace
    """
    Traverse the project directory until a config file is found or the
    filesystem root is reached. If found, use overrides from config as
    project-specific settings.
    """
    dir_path = os.path.dirname(os.path.abspath(options.file))
    config_file_path = os.path.join(dir_path, '.pycheckers')
    while True:
        if os.path.exists(config_file_path):
            options = update_options_from_file(options, config_file_path)
            if not options.merge_configs:
                # We found a file and parsed it, now we're done
                break
        parent = os.path.dirname(dir_path)
        if parent == dir_path:
            break
        dir_path = parent
        config_file_path = os.path.join(dir_path, '.pycheckers')

    return options


def run_one_checker(ignore_codes, options, source_file, checker_name):
    # type: (Tuple[str, ...], Namespace, str, str) -> Tuple[int, List[str]]
    checker_class = RUNNERS[checker_name]
    runner = checker_class(
        ignore_codes=ignore_codes, options=options)
    errors_or_warnings, out_lines = runner.run(source_file)
    return (errors_or_warnings, out_lines)


def find_vcs_root(source_file):
    # type: (str) -> Tuple[Optional[str], Optional[str]]

    def _is_vcs_root(dir_):
        # type: (str) -> str
        for part in ['.git', '.svn', '.hg', '.cvs', '.jedi']:
            path = os.path.join(dir_, part)
            if os.path.exists(path) and os.path.isdir(path):
                return part[1:]             # return the name of the vcs system
        return ''

    cur_dir = os.path.dirname(source_file)
    while True:
        vcs_name = _is_vcs_root(cur_dir)
        if vcs_name:
            return cur_dir, vcs_name
        parent = os.path.dirname(cur_dir)
        if parent == cur_dir:
            break              # Hit the FS root without finding VCS info
        cur_dir = parent
    return None, None


def get_vcs_branch_name(source_file):
    # type: (str) -> Optional[str]
    """If under source control and the VCS supports branches, find branch name.
    """
    # TODO: only supports git for now

    commands = {
        'git': ['git', 'symbolic-ref', '--short', 'HEAD'],
    }
    _vcs_root, vcs_name = find_vcs_root(source_file)
    if not vcs_name or vcs_name not in commands:
        # Unsupported VCS
        return None

    dirname = os.path.dirname(source_file)
    args = commands[vcs_name]
    p = Popen(args, stdout=PIPE, stderr=PIPE, cwd=dirname)
    out, _err = p.communicate()
    p.wait()
    out = out.strip()

    return out if out else None


def find_correct_virtualenv(source_file):
    # type: (str) -> Tuple[Optional[str], Optional[str]]
    """Return the virtualenv that corresponds to this source file, if any, plus
    the project root.

    The virtualenv name must match the name of one of the containing
    directories.

    """
    # TODO: this is very unix-specific
    full_path = os.path.abspath(source_file)
    dir_components = os.path.dirname(full_path).split('/')
    # TODO: this should be a setting
    virtualenv_base = os.path.expanduser('~/.virtualenvs/')
    used_components = []
    for component in dir_components:
        if not component:
            continue
        used_components.append(component)
        virtualenv_path = os.path.join(virtualenv_base, component)
        if os.path.exists(virtualenv_path):
            return virtualenv_path, os.path.join(*used_components)
    return None, None


def update_env_with_virtualenv(source_file):
    # type: (str) -> None
    """Determine if the current file is part of a package that has a
    virtualenv, and munge paths appropriately"""

    venv_path, _project_root = find_correct_virtualenv(source_file)
    if venv_path:
        bin_path = os.path.join(venv_path, 'bin')
        os.environ['PATH'] = bin_path + ':' + os.environ['PATH']


def find_project_root(source_file):
    # type: (str) -> str
    """Find the root of the current project.

    - Based on ~/.emacs.d/plugins/jedi-local.el, look for a VCS directory.
    - Failing that, find a virtualenv that matches a part of the directory.
    - Otherwise, just use the local directory.
    """
    # Case 1
    vcs_root, _vcs_name = find_vcs_root(source_file)
    if vcs_root:
        return vcs_root

    # Case 2
    _venv_root, project_dir = find_correct_virtualenv(source_file)
    if project_dir:
        return project_dir

    # Case 3 - couldn't find a project directory, just use the source_file's
    # parent
    return os.path.dirname(source_file)


def parse_args():

    def str2bool(v):
        if v.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        elif v.lower() in ('no', 'false', 'f', 'n', '0'):
            return False
        else:
            raise ArgumentTypeError('Boolean value expected.')

    parser = ArgumentParser()
    parser.add_argument('file', type=str, help='Filename to check')
    parser.add_argument("-c", "--checkers", dest="checkers",
                        default=default_checkers,
                        help="Comma-separated list of checkers")
    parser.add_argument("-i", "--ignore_codes", dest="ignore_codes",
                        default=','.join(default_ignore_codes),
                        help="Comma-separated list of error codes to ignore")
    parser.add_argument('--max-line-length', dest='max_line_length',
                        default=80, action='store',
                        help='Maximum line length')
    parser.add_argument('--no-merge-configs', dest='merge_configs',
                        action='store_false',
                        help=('Whether to ignore config files found at a '
                              'higher directory than this one'))
    parser.add_argument('--multi-thread', type=str2bool, default=True, action='store',
                        help='Run checkers sequentially, rather than simultaneously')
    parser.add_argument('--pylint-rcfile', default='.pylintrc', dest='pylint_rcfile',
                        help='Location of a config file for pylint')
    return parser.parse_args()


def main():
    # transparently add a virtualenv to the path when launched with a venv'd
    # python. We can sometimes count on emacs to launch us with the correct
    # python, but we need to handle being run manually, or with emacs in a
    # confused state.
    os.environ['PATH'] = (os.path.dirname(sys.executable) + ':' +
                          os.environ['PATH'])

    options = parse_args()

    source_file = options.file
    checkers = options.checkers
    ignore_codes = tuple(options.ignore_codes.split(","))

    options = update_options_locally(options)
    update_env_with_virtualenv(source_file)

    checker_names = [checker.strip() for checker in checkers.split(',')]
    try:
        [RUNNERS[checker_name] for checker_name in checker_names]
    except KeyError:
        croak(("Unknown checker %s" % checker_name),
              ("Expected one of %s" % ', '.join(RUNNERS.keys())))

    if options.multi_thread:
        from multiprocessing import Pool
        p = Pool(5)

        func = partial(run_one_checker, ignore_codes, options, source_file)

        outputs = p.map(func, checker_names)
        p.close()
        p.join()
        counts, out_lines_list = zip(*outputs)
        errors_or_warnings = sum(counts)
    else:
        errors_or_warnings = 0
        out_lines_list = []
        for checker_name in checker_names:
            e_or_w, o_l = run_one_checker(
                ignore_codes, options, source_file, checker_name)
            errors_or_warnings += e_or_w
            out_lines_list.append(o_l)

    for out_lines in out_lines_list:
        for line in out_lines:
            print line

    sys.exit(errors_or_warnings > 0)


if __name__ == '__main__':
    main()
