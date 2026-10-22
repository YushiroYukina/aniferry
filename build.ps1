param([string]$Python = 'python', [string]$Name = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$version = & $Python -c "from branding import VERSION; print(VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read project version' }
if (!$Name) { $Name = 'AniFerry-' + $version.Trim() }
if ($Name -notmatch '^[a-zA-Z0-9._-]+$') { throw 'Name must be a filename without directory components' }
& $Python -m unittest -v test_core.py test_ui.py test_details.py test_downloaders.py
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& $Python tools/build_support.py
if ($LASTEXITCODE -ne 0) { throw 'Release metadata or license collection failed' }
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name $Name --icon assets/aniferry.ico --add-data 'assets;assets' --additional-hooks-dir tools/hooks --version-file build/version-info.txt --distpath dist --workpath build app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed; install PyInstaller in the selected Python environment' }
$tlsDirectory = Join-Path $PWD ('build/tls-' + $Name)
$tlsResult = Join-Path $tlsDirectory 'tls-check.json'
if (Test-Path -LiteralPath $tlsResult) { Remove-Item -LiteralPath $tlsResult }
$tlsProcess = Start-Process -FilePath (Join-Path $PWD ('dist/' + $Name + '/' + $Name + '.exe')) -ArgumentList @('--tls-check', '--data-dir', ('"' + $tlsDirectory + '"')) -WindowStyle Hidden -PassThru
if (!$tlsProcess.WaitForExit(15000)) { throw 'Packaged TLS initialization timed out' }
if ($tlsProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $tlsResult)) { throw 'Packaged TLS initialization failed' }
$tls = Get-Content -LiteralPath $tlsResult -Raw | ConvertFrom-Json
if (!$tls.ok -or !$tls.hostname_check -or $tls.verify_mode -ne 2) { throw 'Packaged TLS validation must retain certificate and hostname checks' }
Write-Output 'Packaged TLS initialization and certificate/hostname verification passed'
Copy-Item -LiteralPath README.md -Destination (Join-Path dist $Name) -Force
Copy-Item -LiteralPath LICENSE,THIRD_PARTY_NOTICES.md -Destination (Join-Path dist $Name) -Force
Copy-Item -LiteralPath docs -Destination (Join-Path dist $Name) -Recurse -Force
Copy-Item -LiteralPath build/licenses -Destination (Join-Path dist $Name) -Recurse -Force
Compress-Archive -Path (Join-Path dist $Name) -DestinationPath (Join-Path dist ($Name + '-Windows-x64.zip')) -Force
