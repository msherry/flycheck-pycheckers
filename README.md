# flycheck-pycheckers
Multiple syntax checker for Python


Copyright Marc Sherry <msherry@gmail.com>

## Summary

This package provides a way to run multiple syntax checkers on Python code.
The list of supported checkers includes:

- pylint
- flake8
- pep8
- pyflakes
- mypy (for both Python 2 and 3)

This is an alternative way of running multiple Python syntax checkers in Emacs
using flycheck that doesn't depend on flycheck's chaining mechanism.

flycheck is [opinionated](https://github.com/flycheck/flycheck/issues/185)
about what checkers should be run, and chaining is difficult to get right
(e.g. see https://github.com/flycheck/flycheck/issues/836).  This package
assumes that the user knows what they want, and can configure their checkers
accordingly -- if they want to run both flake8 and pylint, that's fine.

This also allows us to run multiple syntax checkers in parallel, rather than
sequentially.

## Usage

In your `init.el`:

```elisp
(require 'flycheck-pycheckers) ; Not necessary if using ELPA package
(with-eval-after-load 'flycheck
   (add-hook 'flycheck-mode-hook #'flycheck-pycheckers-setup))
```
