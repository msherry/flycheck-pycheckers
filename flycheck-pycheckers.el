;;; flycheck-pycheckers.el --- multiple syntax checker for Python, using Flycheck

;; Copyright Marc Sherry <msherry@gmail.com>
;; Homepage: https://github.com/msherry/flycheck-pycheckers
;; Version: 0.11.0
;; Package-Requires: ((flycheck "0.18"))
;; Keywords: convenience, tools, languages

;; This file is not part of GNU Emacs.

;; This file is free software; you can redistribute it and/or modify
;; it under the terms of the GNU General Public License as published by
;; the Free Software Foundation; either version 3, or (at your option)
;; any later version.

;; This file is distributed in the hope that it will be useful,
;; but WITHOUT ANY WARRANTY; without even the implied warranty of
;; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;; GNU General Public License for more details.

;; You should have received a copy of the GNU General Public License
;; along with this program.  If not, see <https://www.gnu.org/licenses/>.

;;; Commentary:
;; Copyright Marc Sherry <msherry@gmail.com>
;;
;; This package provides a way to run multiple syntax checkers on Python code,
;; in parallel.  The list of supported checkers includes:
;;
;; - pylint
;; - flake8
;; - pep8
;; - pyflakes
;; - mypy (for both Python 2 and 3)
;; - bandit
;;
;; This is an alternative way of running multiple Python syntax checkers in
;; Flycheck that doesn't depend on Flycheck's chaining mechanism.

;; Flycheck is opinionated about what checkers should be run (see
;; https://github.com/flycheck/flycheck/issues/185), and chaining is difficult
;; to get right (e.g. see https://github.com/flycheck/flycheck/issues/836,
;; https://github.com/flycheck/flycheck/issues/1300).  This package assumes
;; that the user knows what they want, and can configure their checkers
;; accordingly -- if they want to run both flake8 and pylint, that's fine.
;;
;; This also allows us to run multiple syntax checkers in parallel, rather than
;; sequentially.
;;
;; Quick start:
;;
;; Installation via MELPA is easiest:
;;
;;     M-x install-package flycheck-pycheckers
;;
;; Then, in your `init.el':
;;
;; (global-flycheck-mode 1)
;; (with-eval-after-load 'flycheck
;;   (add-hook 'flycheck-mode-hook #'flycheck-pycheckers-setup))
;;
;; Start editing a Python file!
;;
;; For more details on using Flycheck in general, please see
;; http://www.flycheck.org/en/latest/user/quickstart.html.  The error list
;; (viewable with `flycheck-list-errors', bound to `C-c ! l' by default) shows
;; a unified view of all errors found by all checkers, with line and column
;; information where available.
;;
;; flycheck-pycheckers can run over any Python file right away, without needing
;; to set up virtual environments or driver scripts.  You will simply need to
;; ensure that the checkers you want to run (pep8, mypy, flake8, etc.) are
;; installed somewhere on your PATH where Emacs can find them.
;;
;; Alternatives:
;;
;; * Other Flycheck-based checkers -
;;   http://www.flycheck.org/en/latest/languages.html#python.  Some are
;;   officially part of the Flycheck package, and some (like this one) are
;;   external plugins.
;;
;; * Flymake - https://www.emacswiki.org/emacs/FlyMake.  Flymake is an older
;;   syntax-checking minor mode for Emacs, and is generally less supported and
;;   featureful than Flycheck.
;;
;; Configuration options:
;;
;; _You can use this package without needing to get into these details at first
;; -- they are intended for power users and those needing more customization._
;;
;; There are a number of options that can be customized via
;; `customize-variable', which all start with `flycheck-pycheckers-`.  These
;; include:
;;
;; * `flycheck-pycheckers-args' - general arguments to pass to `pycheckers.py'.
;;
;; * `flycheck-pycheckers-checkers' - the set of checkers to run (pylint, pep8,
;;    mypy, etc.).  Can be set in `.pycheckers' files with the variable
;;    `checkers' as a comma-separated list of checker names.
;;
;; * `flycheck-pycheckers-ignore-codes' - a set of error codes to universally
;;   ignore.  These can be set more granularly (e.g. per-project) using the
;;   `.pycheckers' file described below.
;;
;; * `flycheck-pycheckers-max-line-length' - the default maximum line
;;   length.  Can be overridden via `.pycheckers' file.
;;
;; * `flycheck-pycheckers-multi-thread' - whether to run each checker
;;   simultaneously in its own thread, for performance.
;;
;; * `flycheck-pycheckers-venv-root' - a directory containing Python virtual
;;   environments, so that imports may be found.
;;
;; Additionally, a `.pycheckers' file may be created in a directory to control
;; options for every file under this directory.  These files may be logically
;; combined, so a project may have one set of options that may be selectively
;; overridden in a specific subdirectory.
;;
;; Example .pycheckers file:
;;
;;     [DEFAULT]
;;     max_line_length = 120
;;     mypy_config_file = ci/mypy.ini
;;
;; Variables that can be set in the configuration file include the following.
;; Note that these are implemented as modifying the values received by
;; `argparse' in the `pycheckers.py' script, so running `bin/pycheckers.py
;; --help` is a good way to find other options that may be specified.
;;
;; * `max-line-length' - the maximum allowable line-length.  This is a good
;;   option to place in a project-specific directory if you have a personal
;;   line length preference set by default via
;;   `flycheck-pycheckers-max-line-length', but also work on projects that
;;   follow different standards.
;;
;; * `checkers' - a comma-separated list of checkers to be run for files under
;;   this directory.  If, for instance, pep8 should not be run on a directory of
;;   auto-generated code, this option can accomplish that.
;;
;; * `ignore_codes' - a comma-separated list of error/warning codes to ignore
;;   for files under this directory.  Replaces the current set of codes
;;   completely.
;;
;; * `merge_configs' - whether to keep traversing upwards when parsing
;;   `.pycheckers' files, or stop at this one.
;;
;; * `extra_ignore_codes' - a comma-separated list of error/warning codes to
;;   add to the current set of ignored errors.  This can be used to make
;;   certain directories conform to different levels of syntax enforcement.
;;   For example, a directory containing auto-generated code may omit various
;;   warnings about indentation or code style.
;;
;; * `pylint_rcfile' - the location of a project-specific configuration file
;;   for pylint
;;
;; * `mypy_config_file' - the location of a project-specific configuration file
;;   for mypy
;;
;; * `flake8_config_file' - the location of a project-specific configuration file
;;   for flake8

;;; Code:
(require 'flycheck)

(defvar flycheck-pycheckers-command
  (executable-find (concat (file-name-directory (or load-file-name buffer-file-name))
                           "bin/pycheckers.py")))

(flycheck-def-args-var flycheck-pycheckers-args python-pycheckers
  )

;;; TODO: flycheck doesn't seem to support multiple config files -- work around
;;; this
(flycheck-def-config-file-var flycheck-pycheckers-pylintrc python-pycheckers
  ".pylintrc"
  :safe #'stringp)

(flycheck-def-option-var flycheck-pycheckers-checkers '(pylint mypy2 mypy3) python-pycheckers
  "The set of enabled checkers to run."
  :type '(set
          (const :tag "pylint" pylint)
          (const :tag "PEP8" pep8)
          (const :tag "flake8" flake8)
          (const :tag "pyflakes" pyflakes)
          (const :tag "mypy 2" mypy2)
          (const :tag "mypy 3" mypy3)
          (const :tag "bandit" bandit)))

(flycheck-def-option-var flycheck-pycheckers-ignore-codes
    '("C0411" "C0413" "C0103" "C0111" "W0142" "W0201" "W0232" "W0403" "W0511"
      "E1002" "E1101" "E1103" "R0201" "R0801" "R0903" "R0904" "R0914")
  python-pycheckers
  "A list of error codes to ignore.

A nil value (or empty list) means that this option will not be
used at all, and instead, ignored error codes will come from any
config files, if found.  A value of `none' (the symbol) means that
no codes will be ignored -- i.e., config file options will not be
used, and all errors will be reported.

Can be further customized via the \".pycheckers\" config file."
  :type '(radio :tag "Ignored errors"
          (repeat :tag "Codes (overrides config files)" (string :tag "Error/Warning code"))
          (const :tag "Don't ignore any errors (report everything). Overrides config files." none)))

(define-obsolete-variable-alias 'flycheck-pycheckers-enabled-codes 'flycheck-pycheckers-enable-codes)
(flycheck-def-option-var flycheck-pycheckers-enable-codes
    '("W0613")
  python-pycheckers
  "A list of error codes to enable.

Useful for overriding defaults set in a company-wide .pylintrc,
for example.  Can be further customized via the \".pycheckers\"
config file."
  :type '(repeat :tag "Codes" (string :tag "Error/Warning code")))

(flycheck-def-option-var flycheck-pycheckers-max-line-length 79
  python-pycheckers
  "The maximum line length allowed by the checkers."
  :type 'integer)

(flycheck-def-option-var flycheck-pycheckers-multi-thread "true"
    python-pycheckers
  "Whether to run multiple checkers simultaneously."
  :type '(radio (const :tag "Multi-threaded" "true")
          (const :tag "Single-threaded" "false")))

(flycheck-def-option-var flycheck-pycheckers-venv-root "~/.virtualenvs"
   python-pycheckers
   "Directory containing the collection of virtual environments."
   :type 'string)

(flycheck-def-option-var flycheck-pycheckers-report-errors-inline "true"
   python-pycheckers
   "Whether to splice failing checkers' STDERR inline with other errors.

This is mainly used to debug pycheckers.py itself, along with the
checkers that it runs.  When a checker fails for some reason,
e.g. https://github.com/msherry/flycheck-pycheckers/issues/6,
this will report the error in the `flycheck-list-errors' buffer."
   :type '(radio (const :tag "Yes" "true")
           (const :tag "No" "false")))

(flycheck-define-command-checker 'python-pycheckers
  "Multiple python syntax checker.

You can use `customize' to change the default values used, and
directory-specific `.pycheckers' files to customize things
per-directory."

  :command `(,flycheck-pycheckers-command
             (eval flycheck-pycheckers-args)
             ;; When `flycheck-pycheckers-ignore-codes' is a (non-nil) list,
             ;; use it. When nil (empty list), omit the parameter entirely,
             ;; falling back to config files. Any other value means "ignore
             ;; nothing" (report all errors).
             (eval (when flycheck-pycheckers-ignore-codes
                     (concat "--ignore-codes=" (when (listp flycheck-pycheckers-ignore-codes)
                                                 (mapconcat 'identity flycheck-pycheckers-ignore-codes ",")))))
             (eval (when flycheck-pycheckers-enable-codes
                     (concat "--enable-codes=" (when (listp flycheck-pycheckers-enable-codes)
                                                 (mapconcat 'identity flycheck-pycheckers-enable-codes ",")))))
             "--checkers" (eval (mapconcat #'symbol-name flycheck-pycheckers-checkers ","))
             (option "--max-line-length" flycheck-pycheckers-max-line-length nil number-to-string)
             (option "--multi-thread" flycheck-pycheckers-multi-thread)
             (option "--venv-root" flycheck-pycheckers-venv-root)
             (option "--report-checker-errors-inline" flycheck-pycheckers-report-errors-inline)
             (config-file "--pylint-rcfile" flycheck-pycheckers-pylintrc)
             ;; Need `source-inplace' for relative imports (e.g. `from .foo
             ;; import bar'), see https://github.com/flycheck/flycheck/issues/280
             source-inplace)
  :error-patterns
  '((error line-start
     "ERROR " (optional (id (one-or-more (not (any ":"))))) ":"
     (message) " at " (file-name) " line " line (optional "," column) "." line-end)
    (warning line-start
     "WARNING " (optional (id (one-or-more (not (any ":"))))) ":"
     (message) " at " (file-name) " line " line (optional "," column) "." line-end)
    (info line-start
     "INFO " (optional (id (one-or-more (not (any ":"))))) ":"
     (message) " at " (file-name) " line " line (optional "," column) "." line-end))
  :modes 'python-mode)

(defun flycheck-pycheckers-unsetup ()
  "Utility function, used for testing only."
  (interactive)
  (setq flycheck-checkers (remove 'python-pycheckers flycheck-checkers)))

;;;###autoload
(defun flycheck-pycheckers-setup ()
  "Convenience function to setup the pycheckers flycheck checker."
  (interactive)
  ;; *Pre*pend this to 'flycheck-checkers, since we want to use this in
  ;; *preference to all other checkers
  (add-to-list 'flycheck-checkers 'python-pycheckers))

(provide 'flycheck-pycheckers)
;;; flycheck-pycheckers.el ends here
