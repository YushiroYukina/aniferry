"""Capture an explicitly supplied native window, without reading application data."""
def capture_window(root,path):
    import ctypes,struct
    from ctypes import wintypes
    from PIL import Image
    user=ctypes.windll.user32; gdi=ctypes.windll.gdi32
    user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]; user.GetAncestor.restype=wintypes.HWND
    user.GetWindowDC.argtypes=[wintypes.HWND]; user.GetWindowDC.restype=wintypes.HDC
    user.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes=[wintypes.HDC]; gdi.CreateCompatibleDC.restype=wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes=[wintypes.HDC,ctypes.c_int,ctypes.c_int]; gdi.CreateCompatibleBitmap.restype=wintypes.HBITMAP
    gdi.SelectObject.argtypes=[wintypes.HDC,wintypes.HANDLE]; gdi.SelectObject.restype=wintypes.HANDLE
    gdi.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]
    gdi.DeleteObject.argtypes=[wintypes.HANDLE]; gdi.DeleteDC.argtypes=[wintypes.HDC]
    user.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]
    handle=user.GetAncestor(root.winfo_id(),2); rect=wintypes.RECT(); user.GetWindowRect(handle,ctypes.byref(rect))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    dc=user.GetWindowDC(handle); target=gdi.CreateCompatibleDC(dc); bitmap=gdi.CreateCompatibleBitmap(dc,width,height)
    old=gdi.SelectObject(target,bitmap)
    try:
        if not user.PrintWindow(handle,target,2): raise RuntimeError('PrintWindow failed')
        info=ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII',40,width,-height,1,32,0,width*height*4,0,0,0,0))
        pixels=ctypes.create_string_buffer(width*height*4)
        if not gdi.GetDIBits(target,bitmap,0,height,pixels,info,0): raise RuntimeError('GetDIBits failed')
        Image.frombytes('RGB',(width,height),pixels.raw,'raw','BGRX').save(path)
    finally:
        gdi.SelectObject(target,old); gdi.DeleteObject(bitmap); gdi.DeleteDC(target); user.ReleaseDC(handle,dc)

