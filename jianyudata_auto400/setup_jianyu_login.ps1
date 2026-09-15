$ErrorActionPreference = 'Stop'
$username = Read-Host 'Jianyu phone number or account name'
if ([string]::IsNullOrWhiteSpace($username)) { throw 'Account name cannot be empty.' }
$secret = Read-Host 'Jianyu password' -AsSecureString
$credential = [pscredential]::new($username.Trim(), $secret)
$credential | Export-Clixml -LiteralPath (Join-Path $PSScriptRoot 'jianyu_credential.xml')
Write-Host 'Saved encrypted Jianyu credentials for this Windows user. No login was attempted.'
