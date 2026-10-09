"""Windows taskbar identity and single-window launch for Guanlan.

Only the exact observer app window is tagged. The shared Edge process and
ordinary browser windows retain their own identities.
"""
import ctypes
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from guanlan_ui import ENTRY_TITLES

APP_ID = 'TingFengGuanLan.Portable'
APP_IDS = {name: APP_ID for name in ENTRY_TITLES}
ICON = Path(__file__).parent / 'static' / 'tingfeng-guanlan-icon-v2.ico'



class GUID(ctypes.Structure):
    _fields_ = [('data1', ctypes.c_uint32), ('data2', ctypes.c_uint16),
                ('data3', ctypes.c_uint16), ('data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', ctypes.c_uint32)]


class CountedPointer(ctypes.Structure):
    _fields_ = [('count', ctypes.c_uint32), ('pointer', ctypes.c_void_p)]


class VariantValue(ctypes.Union):
    _fields_ = [('text', ctypes.c_wchar_p), ('integer', ctypes.c_int64),
                ('array', CountedPointer)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_uint16), ('reserved', ctypes.c_uint16 * 3),
                ('value', VariantValue)]


PROPERTY_IID = GUID.parse('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
APP_FMTID = GUID.parse('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3')
PROPERTY_IDS = {'id': 5, 'command': 2, 'title': 3, 'icon': 4}


def _check(hr):
    if hr < 0:
        raise OSError('Windows application identity: HRESULT %08X' % (hr & 0xffffffff))


class Desktop:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows desktop only')
        from ctypes import wintypes as w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.ole = ctypes.WinDLL('ole32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback, w.LPARAM]
        self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        self.user.SetForegroundWindow.argtypes = [w.HWND]
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        self.kernel.CreateMutexW.restype = w.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.WaitForSingleObject.restype = w.DWORD
        self.kernel.ReleaseMutex.argtypes = [w.HANDLE]
        result_type = ctypes.c_int32
        self.shell.SHGetPropertyStoreForWindow.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.shell.SHGetPropertyStoreForWindow.restype = result_type
        self.shell.SHGetPropertyStoreFromParsingName.argtypes = [w.LPCWSTR, ctypes.c_void_p, w.DWORD,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.shell.SHGetPropertyStoreFromParsingName.restype = result_type
        self.shell.SHChangeNotify.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = result_type
        self.ole.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]

    def _method(self, store, index, *args):
        table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, *args)(table[index])

    @contextmanager
    def _store(self, *, hwnd=None, path=None, write=False):
        initialized = self.ole.CoInitializeEx(None, 2) >= 0
        store = ctypes.c_void_p()
        try:
            if hwnd is not None:
                _check(self.shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(PROPERTY_IID), ctypes.byref(store)))
            else:
                _check(self.shell.SHGetPropertyStoreFromParsingName(str(path), None, 2 if write else 0,
                    ctypes.byref(PROPERTY_IID), ctypes.byref(store)))
            yield store
        finally:
            if store.value:
                self._method(store, 2)(store)
            if initialized:
                self.ole.CoUninitialize()

    def _read(self, store, name):
        variant = PROPVARIANT()
        key = PROPERTYKEY(APP_FMTID, PROPERTY_IDS[name])
        try:
            _check(self._method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
                store, ctypes.byref(key), ctypes.byref(variant)))
            return variant.value.text if variant.vt == 31 else None
        finally:
            self.ole.PropVariantClear(ctypes.byref(variant))

    def _write(self, store, values, *, commit):
        for name, value in values.items():
            variant = PROPVARIANT()
            variant.vt = 31
            variant.value.text = value
            key = PROPERTYKEY(APP_FMTID, PROPERTY_IDS[name])
            _check(self._method(store, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
                store, ctypes.byref(key), ctypes.byref(variant)))
        if commit:
            _check(self._method(store, 7)(store))

    def properties(self, *, hwnd=None, path=None):
        with self._store(hwnd=hwnd, path=path) as store:
            return {name: self._read(store, name) for name in PROPERTY_IDS}

    def identify_shortcut(self, path, module):
        """Installer/repair use: preserve the shortcut target, arguments and icon."""
        with self._store(path=path, write=True) as store:
            self._write(store, {'id': APP_IDS[module]}, commit=True)
        self.shell.SHChangeNotify(0x2000, 5, ctypes.cast(ctypes.c_wchar_p(str(path)), ctypes.c_void_p), None)

    def identify_window(self, hwnd, module, port=18736):
        executable = Path(sys.executable).with_name('pythonw.exe')
        url=getattr(self,'url',f'http://127.0.0.1:{port}/home/')
        command = subprocess.list2cmdline([str(executable), '-m', 'guanlan_app', 'open', '--url', url])
        # Relaunch metadata precedes the ID, so a window pinned during startup
        # will reopen the correct module via the silent launcher.
        with self._store(hwnd=hwnd) as store:
            self._write(store, {'command': command, 'title': '听风观澜',
                'icon': str(ICON) + ',0', 'id': APP_IDS[module]}, commit=False)

    @contextmanager
    def window_launch(self, port=18736):
        handle = self.kernel.CreateMutexW(None, False, 'Local\\TingFengGuanLan.Portable.Window.' + str(port))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        owned = False
        try:
            status = self.kernel.WaitForSingleObject(handle, 30000)
            if status not in (0, 0x80):
                raise OSError('听风观澜窗口正在启动，请稍后再试')
            owned = True
            yield
        finally:
            if owned:
                self.kernel.ReleaseMutex(handle)
            self.kernel.CloseHandle(handle)

    def observer_windows(self, module=None):
        from ctypes import wintypes as w
        result = []
        selected = [ENTRY_TITLES[module]] if module is not None else ENTRY_TITLES.values()
        titles = {name + ' · StockOperator' for name in selected}
        titles.update('观澜 · ' + name for name in selected)
        titles.update('听风观澜 · ' + name for name in selected)
        @self.callback
        def visit(hwnd, _):
            text = ctypes.create_unicode_buffer(512)
            cls = ctypes.create_unicode_buffer(128)
            self.user.GetWindowTextW(hwnd, text, len(text))
            self.user.GetClassNameW(hwnd, cls, len(cls))
            if not (self.user.IsWindowVisible(hwnd) and text.value in titles and cls.value == 'Chrome_WidgetWin_1'):
                return True
            pid = w.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self.kernel.OpenProcess(0x1000, False, pid.value)
            if process:
                try:
                    image = ctypes.create_unicode_buffer(32768)
                    size = w.DWORD(len(image))
                    if (self.kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size))
                            and Path(image.value).name.lower() == 'msedge.exe'):
                        result.append(hwnd)
                finally:
                    self.kernel.CloseHandle(process)
            return True
        self.user.EnumWindows(visit, 0)
        return result

    def activate(self, hwnd):
        self.user.ShowWindow(hwnd, 9)
        self.user.SetForegroundWindow(hwnd)

    def identify_inspector(self, nonce, port=18736):
        """Tag only a nonce-claimed detail in an already tagged app's process."""
        import re
        from ctypes import wintypes as w
        if not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ValueError('详情窗口标识无效')
        owners=set()
        for hwnd in self.observer_windows():
            if self.properties(hwnd=hwnd)['id'] == APP_ID:
                pid=w.DWORD();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid));owners.add(pid.value)
        claimed=[]
        title='听风观澜 · K线详情 · '+nonce
        @self.callback
        def visit(hwnd, _):
            text=ctypes.create_unicode_buffer(512);cls=ctypes.create_unicode_buffer(128);pid=w.DWORD()
            self.user.GetWindowTextW(hwnd,text,len(text));self.user.GetClassNameW(hwnd,cls,len(cls))
            self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if self.user.IsWindowVisible(hwnd) and text.value==title and cls.value=='Chrome_WidgetWin_1' and pid.value in owners:
                claimed.append(hwnd)
            return True
        self.user.EnumWindows(visit,0)
        if len(claimed)!=1:return False
        self.identify_window(claimed[0],'industry30',port=port)
        return True


def open_url(url):
    """Use the existing Edge app shell on Windows; ordinary browser elsewhere."""
    if os.name!='nt':
        import webbrowser
        webbrowser.open(url);return
    from urllib.parse import urlsplit
    import time
    port=urlsplit(url).port or 443
    edge=Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
    if not edge.is_file():
        import webbrowser
        webbrowser.open(url);return
    desktop=Desktop();desktop.url=url
    with desktop.window_launch(port):
        before=set(desktop.observer_windows())
        for hwnd in before:
            props=desktop.properties(hwnd=hwnd)
            if props.get('id')==APP_ID and url in (props.get('command') or ''):
                desktop.activate(hwnd);return
        subprocess.Popen([str(edge),'--app='+url],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            found=set(desktop.observer_windows())-before
            if len(found)==1:
                desktop.identify_window(next(iter(found)),'home',port);return
            time.sleep(.1)
