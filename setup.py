from setuptools import setup, find_packages

VERSION = '0.0.1' 
DESCRIPTION = 'Convert python 3 program with typehint into python 2'

setup(
        name="py3to2", 
        version=VERSION,
        python_requires='>=3.7.0',
        author="Wayho Wang",
        author_email="<wweihao@outlook.com>",
        description=DESCRIPTION,
        packages=find_packages(),
        install_requires=['libcst', '3to2', 'pytype'], 
        keywords=['python'], 
        entry_points ={
            'console_scripts': [
                'py3to2 = py3to2.main:main'
            ]
        },
        classifiers= [
            "Programming Language :: Python :: 3",
        ]
)