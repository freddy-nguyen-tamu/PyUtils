# List-RecycleBin.ps1
# Run with Windows PowerShell 5.1 or newer.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class RecycleBinNative
{
    [DllImport("Shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHEmptyRecycleBin(
        IntPtr hwnd,
        string pszRootPath,
        uint dwFlags
    );

    public const uint SHERB_NOCONFIRMATION = 0x00000001;
    public const uint SHERB_NOPROGRESSUI  = 0x00000002;
    public const uint SHERB_NOSOUND       = 0x00000004;
}
"@

function Format-FileSize {
    param(
        [long]$Bytes
    )

    if ($Bytes -ge 1TB) {
        return "{0:N2} TB" -f ($Bytes / 1TB)
    }
    elseif ($Bytes -ge 1GB) {
        return "{0:N2} GB" -f ($Bytes / 1GB)
    }
    elseif ($Bytes -ge 1MB) {
        return "{0:N2} MB" -f ($Bytes / 1MB)
    }
    elseif ($Bytes -ge 1KB) {
        return "{0:N2} KB" -f ($Bytes / 1KB)
    }

    return "$Bytes bytes"
}

try {
    $shell = New-Object -ComObject Shell.Application

    # Shell namespace 10 is the Windows Recycle Bin.
    $recycleBin = $shell.Namespace(10)

    if ($null -eq $recycleBin) {
        throw "Windows could not open the Recycle Bin."
    }

    $items = @($recycleBin.Items())

    if ($items.Count -eq 0) {
        Write-Host "`nThe Recycle Bin is empty." -ForegroundColor Green
        exit 0
    }

    $results = foreach ($item in $items) {
        $name = [string]$item.Name

        # Folders do not normally have a meaningful extension.
        $extension = if ($item.IsFolder) {
            "[Folder]"
        }
        else {
            $ext = [System.IO.Path]::GetExtension($name)

            if ([string]::IsNullOrWhiteSpace($ext)) {
                "[No extension]"
            }
            else {
                $ext.ToLowerInvariant()
            }
        }

        $sizeBytes = 0L

        try {
            $sizeBytes = [long]$item.Size
        }
        catch {
            $sizeBytes = 0L
        }

        [PSCustomObject]@{
            Name      = $name
            Format    = $extension
            ItemType  = [string]$item.Type
            Size      = Format-FileSize -Bytes $sizeBytes
            SizeBytes = $sizeBytes
        }
    }

    Write-Host "`nItems currently in the Recycle Bin:`n" -ForegroundColor Cyan

    $results |
        Select-Object Name, Format, ItemType, Size |
        Format-Table -AutoSize -Wrap

    $totalBytes = ($results | Measure-Object -Property SizeBytes -Sum).Sum

    Write-Host ("Total items: {0}" -f $results.Count)
    Write-Host ("Approximate total size: {0}" -f (
        Format-FileSize -Bytes ([long]$totalBytes)
    ))

    Write-Host "`nWARNING: Deleting these items cannot be undone." `
        -ForegroundColor Yellow

    $choice = Read-Host @"

Enter DELETE ALL to permanently delete every item.
Press Enter, or type anything else, to do nothing
"@

    if ($choice.Trim() -ceq "DELETE ALL") {
        $flags =
            [RecycleBinNative]::SHERB_NOCONFIRMATION -bor
            [RecycleBinNative]::SHERB_NOPROGRESSUI -bor
            [RecycleBinNative]::SHERB_NOSOUND

        $result = [RecycleBinNative]::SHEmptyRecycleBin(
            [IntPtr]::Zero,
            $null,
            $flags
        )

        if ($result -eq 0) {
            Write-Host "`nThe Recycle Bin was emptied successfully." `
                -ForegroundColor Green
        }
        else {
            throw "Windows returned error code $result while emptying the Recycle Bin."
        }
    }
    else {
        Write-Host "`nNo items were deleted." -ForegroundColor Green
    }
}
catch {
    Write-Error "Operation failed: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($null -ne $shell) {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}