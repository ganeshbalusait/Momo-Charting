# One-time: trust the PC's Schwab login catcher so Chrome stops saying
# "Your connection is not private" after the Schwab login (2026-11-04, "Renew both").
#
# The certificate only covers 127.0.0.1 / localhost and cannot sign other
# certificates (CA:false), so trusting it lets nothing else through. It is made
# by schwab_oauth_callback.ensure_localhost_certificate (3-year life); when it is
# renewed, run this again. Windows shows a "Do you want to install this
# certificate?" box - click Yes. Current user only; no admin needed.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
Push-Location $repo
try {
    & $python -c "import schwab_oauth_callback as cb; cb.ensure_localhost_certificate(); print(cb.CERT_PATH)" | Out-Null
} finally {
    Pop-Location
}
$pem = Join-Path $repo "artifacts\localhost_schwab_cert.pem"
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($pem)
# Older copies of our own certificate only (same subject), never anything else.
Get-ChildItem Cert:\CurrentUser\Root |
    Where-Object { $_.Subject -eq $cert.Subject -and $_.Thumbprint -ne $cert.Thumbprint } |
    ForEach-Object { Remove-Item -LiteralPath $_.PSPath }
if (Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }) {
    "Already trusted: $($cert.Thumbprint) (valid to $($cert.NotAfter))"
} else {
    Import-Certificate -FilePath $pem -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    "Trusted: $($cert.Thumbprint) (valid to $($cert.NotAfter))"
}
