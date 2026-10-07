# Third-party software in Takeline

Takeline is built with the following components. Each remains under its own license.

## Qt for Python (PySide6, Shiboken6) and Qt
Copyright (C) The Qt Company Ltd. and other contributors.
Used under the GNU Lesser General Public License version 3 (LGPL v3): https://www.gnu.org/licenses/lgpl-3.0.html
Source code: Qt https://download.qt.io/ - Qt for Python https://code.qt.io/cgit/pyside/pyside-setup.git/
Takeline's own source code and build scripts (build_windows.bat, takeline.spec) let you rebuild the
application against a different or modified version of these libraries.

## Python
Copyright (C) Python Software Foundation. Python Software Foundation License: https://docs.python.org/3/license.html

## tzdata (Python package with the IANA time zone database)
Apache License 2.0: https://github.com/python/tzdata - the IANA database itself is in the public domain.

## PyInstaller bootloader
GNU GPL v2 with a special exception that allows distributing applications built with PyInstaller under
any license: https://pyinstaller.org/en/stable/license.html

## ExifTool (only if the application was built with -WithExifTool)
Copyright (C) Phil Harvey. Free software; distributed under the same terms as Perl itself
(Perl Artistic License or GNU GPL): https://exiftool.org/
