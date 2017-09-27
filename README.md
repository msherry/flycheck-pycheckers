[![MELPA](https://melpa.org/packages/flycheck-pycheckers-badge.svg)](https://melpa.org/#/flycheck-pycheckers)
[![MELPA](https://stable.melpa.org/packages/flycheck-pycheckers-badge.svg)](https://stable.melpa.org/#/flycheck-pycheckers)

# flycheck-pycheckers
Multiple syntax checker for Python

Copyright Marc Sherry <msherry@gmail.com>

This package provides a way to run multiple syntax checkers on Python code,
in parallel.  The list of supported checkers includes:

- pylint
- flake8
- pep8
- pyflakes
- mypy (for both Python 2 and 3)

This is an alternative way of running multiple Python syntax checkers in
flycheck that doesn't depend on flycheck's chaining mechanism.

flycheck is [opinionated](https://github.com/flycheck/flycheck/issues/185)
about what checkers should be run, and chaining is difficult to get right
(e.g. see https://github.com/flycheck/flycheck/issues/836).  This package
assumes that the user knows what they want, and can configure their checkers
accordingly -- if they want to run both flake8 and pylint, that's fine.

This also allows us to run multiple syntax checkers in parallel, rather than
sequentially.

### Usage

Installation via MELPA is easiest:

```elisp
M-x install-package flycheck-pycheckers
```

In your `init.el`:

```elisp
(require 'flycheck-pycheckers) ; Not necessary if installed via MELPA
(with-eval-after-load 'flycheck
   (add-hook 'flycheck-mode-hook #'flycheck-pycheckers-setup))
```

`flycheck-pycheckers` attempts to make itself the preferred Flycheck checker
for python by adding itself to the beginning of `flycheck-checkers`, which
is traversed in order until a valid checker is found.  The error list
(viewable with `flycheck-list-errors`, bound to <kbd>C-c ! l</kbd> by default) shows
a unified view of all errors found by all checkers, with line and column
information where available.

![flycheck-list-errors](docs/flycheck-list-errors.png "flycheck-list-errors")

### Configuration

There are a number of options that can be customized via
`customize-variable`, which all start with `flycheck-pycheckers-`.  These
include:

* `flycheck-pycheckers-args` - general arguments to pass to `pycheckers.py`.
* `flycheck-pycheckers-checkers` - the set of checkers to run (pylint, pep8,
   mypy, etc.).
* `flycheck-pycheckers-ignore-codes` - a set of error codes to universally
  ignore.  These can be set more granularly (e.g. per-project) using the
  `.pycheckers` file described below.
* `flycheck-pycheckers-max-line-length` - the default maximum line
  length.  Can be overridden via `.pycheckers` file.
* `flycheck-pycheckers-multi-thread` - whether to run each checker
  simultaneously in its own thread, for performance.
* `flycheck-pycheckers-venv-root` - a directory containing Python virtual
  environments, so that imports may be found.

Additionally, a `.pycheckers` file may be created in a directory to control
options for every file under this directory.  These files may be logically
combined, so a project may have one set of options that may be selectively
overridden in a specific subdirectory.

### Example .pycheckers file

    [DEFAULT]
    max_line_length = 120
    mypy_config_file = ci/mypy.ini



---
Converted from `flycheck-pycheckers.el` by [*el2markdown*](https://github.com/Lindydancer/el2markdown).
