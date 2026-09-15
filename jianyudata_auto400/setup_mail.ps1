$ErrorActionPreference='Stop'
$secret=Read-Host 'Paste the Google APP PASSWORD (not the account password)' -AsSecureString
$credential=[pscredential]::new('zihao.zhang@smartx.com',$secret)
$credential | Export-Clixml -LiteralPath (Join-Path $PSScriptRoot 'mail_credential.xml')
Write-Host 'Saved encrypted credentials for this Windows user. No email was sent.'
