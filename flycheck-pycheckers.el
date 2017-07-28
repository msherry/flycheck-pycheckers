;;; flycheck-pycheckers.el --- multiple syntax checker for Python

;; Copyright Marc Sherry <msherry@gmail.com>
;; Homepage: https://github.com/msherry/flycheck-pycheckers
;; Version: 0.1
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
;; along with this program.  If not, see <http://www.gnu.org/licenses/>

;;; Commentary:

;; This package provides a way to run multiple syntax checkers on Python
;; code.  The list of supported checkers includes:
;;
;; - pylint
;; - flake8
;; - pep8
;; - pyflakes
;; - mypy (for both Python 2 and 3)
;;
;; This is an alternative way of running multiple Python syntax checkers in
;; flycheck that doesn't depend on flycheck's chaining mechanism.  flycheck is
;; opinionated about what checkers should be run (see
;; https://github.com/flycheck/flycheck/issues/185), and chaining is difficult
;; to get right (e.g. see https://github.com/flycheck/flycheck/issues/836).
;; This package assumes that the user knows what they want, and can configure
;; their checkers accordingly -- if they want to run both flake8 and pylint,
;; that's fine.
;;
;; This also allows us to run multiple syntax checkers in parallel, rather than
;; sequentially.
;;
;; Usage:
;;
;; In your `init.el':
;; (require 'flycheck-pycheckers) ; Not necessary if using ELPA package
;; (with-eval-after-load 'flycheck
;;   (add-hook 'flycheck-mode-hook #'flycheck-pycheckers-setup))

;;; Code:
(require 'flycheck)

(defvar flycheck-pycheckers-command
  (executable-find (concat (file-name-directory (or load-file-name buffer-file-name))
                           "bin/pycheckers.py")))

(flycheck-def-args-var flycheck-pycheckers-args python-pycheckers
  )

(flycheck-def-config-file-var flycheck-pycheckers-pylintrc python-pycheckers
    ".pylintrc"
  :safe #'stringp)

(flycheck-def-option-var flycheck-pycheckers-checkers '(pylint mypy2 mypy3) python-pycheckers
  "The set of enabled checkers to run"
  :type '(set
          (const :tag "pylint" pylint)
          (const :tag "PEP8" pep8)
          (const :tag "flake8" flake8)
          (const :tag "pyflakes" pyflakes)
          (const :tag "mypy 2" mypy2)
          (const :tag "mypy 3" mypy3)))

(flycheck-def-option-var flycheck-pycheckers-ignore-codes '("C0411"
                                                            "C0413")
    python-pycheckers
  "A list of error codes to ignore"
  :type '(repeat :tag "Codes" (string :tag "Error/Warning code")))

(flycheck-def-option-var flycheck-pycheckers-max-line-length 80
    python-pycheckers
  "The maximum line length allowed by the checkers."
  :type 'integer)

(flycheck-def-option-var flycheck-pycheckers-multi-thread "true"
    python-pycheckers
  "Whether to run multiple checkers simultaneously"
  :type '(radio (const :tag "Multi-threaded" "true")
                 (const :tag "Single-threaded" "false")))

(flycheck-define-command-checker 'python-pycheckers
  "Multiple python syntax checker.

You can use `customize' to change the default values used, and
directory-specific `.pycheckers' files to customize things
per-directory."

  :command `(,flycheck-pycheckers-command
             (eval flycheck-pycheckers-args)
             "-i" (eval (mapconcat 'identity flycheck-pycheckers-ignore-codes ","))
             "-c" (eval (mapconcat #'symbol-name flycheck-pycheckers-checkers ","))
             "--max-line-length" (eval (number-to-string flycheck-pycheckers-max-line-length))
             "--multi-thread" (eval flycheck-pycheckers-multi-thread)
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
            (message) " at " (file-name) " line " line (optional "," column) "." line-end))
  :modes 'python-mode)

;;; ###autoload
(defun flycheck-pycheckers-setup ()
  "Convenience function to setup the pycheckers flycheck checker."
  (interactive)
  ;; *Pre*pend this to 'flycheck-checkers, since we want to use this in
  ;; *preference to all other checkers
  (add-to-list 'flycheck-checkers 'python-pycheckers))

(provide 'flycheck-pycheckers)
;;; flycheck-pycheckers.el ends here
