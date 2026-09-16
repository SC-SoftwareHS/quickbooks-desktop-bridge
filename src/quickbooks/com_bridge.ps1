# Persistent 32-bit COM host for QBXMLRP2.RequestProcessor.
# Protocol (stdin / stdout, one line each):
#   OPEN appName|companyFile|fileMode|connectionType
#   PROCESS requestPath|responsePath
#   CLOSE
# Replies: "OK" or "ERR message"
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$rp = $null
$ticket = $null
$opened = $false
$session = $false

function Unescape-Field([string]$value) {
    return $value.Replace("\|", "|").Replace("\\", "\")
}

function Write-Ok {
    [Console]::Out.WriteLine("OK")
    [Console]::Out.Flush()
}

function Write-Err([string]$message) {
    $safe = $message -replace '[\r\n]+', ' '
    [Console]::Out.WriteLine("ERR $safe")
    [Console]::Out.Flush()
}

function Close-QB {
    if ($session -and $rp -ne $null -and $ticket -ne $null) {
        try { $rp.EndSession($ticket) } catch { }
    }
    $script:session = $false
    $script:ticket = $null
    if ($opened -and $rp -ne $null) {
        try { $rp.CloseConnection() } catch { }
    }
    $script:opened = $false
    $script:rp = $null
}

try {
    while ($true) {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line) { break }
        if ($line.Trim() -eq "") { continue }

        if ($line -eq "CLOSE") {
            Close-QB
            Write-Ok
            break
        }

        if ($line.StartsWith("OPEN ")) {
            try {
                Close-QB
                $payload = $line.Substring(5)
                $parts = [regex]::Split($payload, '(?<!\\)\|')
                $appName = Unescape-Field $parts[0]
                $companyFile = if ($parts.Count -gt 1) { Unescape-Field $parts[1] } else { "" }
                $fileMode = if ($parts.Count -gt 2 -and $parts[2]) { [int]$parts[2] } else { 2 }
                $connType = if ($parts.Count -gt 3 -and $parts[3]) { [int]$parts[3] } else { 1 }

                $script:rp = New-Object -ComObject QBXMLRP2.RequestProcessor
                try {
                    $rp.OpenConnection2("", $appName, $connType)
                } catch {
                    $rp.OpenConnection("", $appName)
                }
                $script:opened = $true
                $script:ticket = $rp.BeginSession($companyFile, $fileMode)
                $script:session = $true
                Write-Ok
            } catch {
                Close-QB
                Write-Err $_.Exception.Message
            }
            continue
        }

        if ($line.StartsWith("PROCESS ")) {
            try {
                if (-not $session) { throw "No open QuickBooks session" }
                $split = [regex]::Split($line.Substring(8), '(?<!\\)\|')
                if ($split.Count -lt 2) { throw "PROCESS requires request and response paths" }
                $reqPath = Unescape-Field $split[0]
                $resPath = Unescape-Field $split[1]
                $request = [System.IO.File]::ReadAllText($reqPath)
                $response = $rp.ProcessRequest($ticket, $request)
                [System.IO.File]::WriteAllText($resPath, $response, [System.Text.UTF8Encoding]::new($false))
                Write-Ok
            } catch {
                Write-Err $_.Exception.Message
            }
            continue
        }

        Write-Err "Unknown command"
    }
} finally {
    Close-QB
}
