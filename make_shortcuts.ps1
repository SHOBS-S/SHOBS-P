# Called by "Create shortcuts.bat". Makes SHOBS-P shortcuts that start the app with pythonw (no console window).
# Each shortcut is also stamped with the app's ID (ShilohHill.SHOBS-P), the same ID the running app reports,
# so Windows treats the shortcut and the window as one app: pinning keeps the SHOBS-P icon, not Python's.
param([string]$AppDir, [string]$PythonW)
$AppDir = (Resolve-Path $AppDir).Path
$AppId = 'ShilohHill.SHOBS-P'

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class CShellLink {}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PROPERTYKEY { public Guid fmtid; public uint pid; }

[StructLayout(LayoutKind.Explicit, Size = 24)]
public struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
interface IPropertyStore {
    [PreserveSig] int GetCount(out uint c);
    [PreserveSig] int GetAt(uint i, out PROPERTYKEY k);
    [PreserveSig] int GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
    [PreserveSig] int SetValue(ref PROPERTYKEY k, ref PROPVARIANT v);
    [PreserveSig] int Commit();
}

public static class ShobsAppId {
    public static void Stamp(string lnk, string id) {
        object link = new CShellLink();
        IPersistFile pf = (IPersistFile)link;
        pf.Load(lnk, 2);  // STGM_READWRITE
        IPropertyStore ps = (IPropertyStore)link;
        PROPERTYKEY key = new PROPERTYKEY();
        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");  // PKEY_AppUserModel_ID
        key.pid = 5;
        PROPVARIANT pv = new PROPVARIANT();
        pv.vt = 31;  // VT_LPWSTR
        pv.p = Marshal.StringToCoTaskMemUni(id);
        try {
            Marshal.ThrowExceptionForHR(ps.SetValue(ref key, ref pv));
            Marshal.ThrowExceptionForHR(ps.Commit());
            pf.Save(lnk, true);
        } finally {
            Marshal.FreeCoTaskMem(pv.p);
        }
    }
}
"@

$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($dir in $places) {
    $path = Join-Path $dir 'SHOBS-P.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $PythonW
    $link.Arguments = '"' + (Join-Path $AppDir 'clearv_app.py') + '"'
    $link.WorkingDirectory = $AppDir
    $link.IconLocation = (Join-Path $AppDir 'shobs_p.ico') + ',0'
    $link.Description = 'Shiloh Hill Observatory - Photometry (SHOBS-P)'
    $link.Save()
    try {
        [ShobsAppId]::Stamp($path, $AppId)
        Write-Host "Created $path (app ID set)"
    } catch {
        Write-Host "Created $path (could not set app ID: $($_.Exception.Message))"
    }
}
