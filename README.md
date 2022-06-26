# py3to2
A converter that enables python 2 with some python 3 features.

## Install
```
pip install git+https://github.com/wayhowang/py3to2.git@main
```

## Usage

Says you write python 3 code in `DIR_PY3`, and you want to the converter write code in `DIR_PY2`.

py3to2 initialize ./build
```


## Description

It is *not* a compiler that compiles every new features introduced in Python 3 into Python 2 code.
It is a converter that supports very limited python2 features.


0. most fixes introduced by `lib2to3`, except `fix_printfunction`, `fix_print`, `fix_absimport` and `fix_annotations`
1. add `# coding=utf8`
2. add `from __future__ import absolute_import, division, print_function, unicode_literals`
3. remove type hint
4. support `typing` and `typing_extensions`
5. remove identifiers that starts with `__cskip_`
6. remove the statement written after `# pyc: skip`


## Disclamer
The project is mainly written for self-use. It is `not` well tested and runs pretty slowly.
But this tool may be helpful if you have to write code for python 2.7 unfortunately.



"""
