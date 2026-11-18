<#
  Устанавливает или обновляет бота на сервере (Oracle Cloud или любой VPS с Ubuntu).

  Обычно запускается через deploy.bat:
      deploy.bat                      спросит IP сервера и путь к ключу
      deploy.bat 140.238.12.34        IP сразу
      deploy.bat -Key D:\keys\my.key  другой ключ
      deploy.bat -Logs                смотреть логи бота на сервере

  IP, ключ и пользователь запоминаются в .deploy.json — в следующий раз
  достаточно просто запустить deploy.bat.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string]$Server,
    [string]$Key,
    [string]$User,
    [int]$Port,
    [switch]$Logs
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Service = 'footbalbot'
$DefaultKey = Join-Path $env:USERPROFILE '.ssh\oracle-bot.key'
$SavedFile = Join-Path $PSScriptRoot '.deploy.json'
$BundleName = 'footbalbot-deploy.tgz'

function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Green }
function Fail([string]$Text) { Write-Host ''; Write-Host "[ошибка] $Text" -ForegroundColor Red; exit 1 }
function Unquote([string]$Text) { if ($Text) { return $Text.Trim().Trim('"').Trim("'") } return '' }

foreach ($tool in 'ssh', 'scp', 'tar') {
    if (-not (Get-Command "$tool.exe" -ErrorAction SilentlyContinue)) {
        Fail "Не найдена программа $tool. Установи «Клиент OpenSSH»: Параметры -> Система -> Дополнительные компоненты."
    }
}

# ------------------------------------------------------------ saved settings
$saved = @{}
if (Test-Path -LiteralPath $SavedFile) {
    try {
        $json = Get-Content -LiteralPath $SavedFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $json.PSObject.Properties) { $saved[$prop.Name] = $prop.Value }
    } catch {
        Write-Warning 'Не удалось прочитать .deploy.json — спрошу всё заново.'
    }
}
if (-not $Server) { $Server = $saved['server'] }
if (-not $Key -and $saved['key']) {
    if (Test-Path -LiteralPath $saved['key'] -PathType Leaf) { $Key = $saved['key'] }
    else { Write-Host "Ключа больше нет по старому пути: $($saved['key'])" -ForegroundColor Yellow }
}
if (-not $User) { $User = $saved['user'] }
if (-not $Port) { $Port = [int]$saved['port'] }

# ------------------------------------------------------------------- server
if (-not $Server) {
    Write-Host 'IP сервера в Oracle Cloud: Compute -> Instances -> твой сервер -> Public IP address.'
    $Server = Read-Host 'IP сервера'
}
$Server = Unquote $Server
if (-not $Server) { Fail 'IP сервера не указан' }
if ($Server -match '^(?<user>[^@\s]+)@(?<host>.+)$') { $User = $Matches['user']; $Server = $Matches['host'] }
if ($Server -notmatch '^[A-Za-z0-9.-]+$') { Fail "Странный адрес сервера: '$Server'. Нужен IP, например 140.238.12.34" }
if (-not $User) { $User = 'ubuntu' }
if (-not $Port) { $Port = 22 }

# ---------------------------------------------------------------------- key
if (-not $Key -and (Test-Path -LiteralPath $DefaultKey)) { $Key = $DefaultKey }
if (-not $Key) {
    Write-Host 'Нужен приватный ключ, скачанный в Oracle при создании сервера (файл ssh-key-....key).'
    Write-Host 'Можно перетащить файл мышкой в это окно и нажать Enter.'
    $Key = Read-Host 'Путь к ключу'
}
$Key = Unquote $Key
if (-not $Key) { Fail 'Путь к ключу не указан' }
if (-not (Test-Path -LiteralPath $Key -PathType Leaf)) { Fail "Файл ключа не найден: $Key" }
$Key = (Resolve-Path -LiteralPath $Key).Path
if ((Get-Content -LiteralPath $Key -TotalCount 1) -match '^(ssh-|ecdsa-)') {
    Fail 'Это публичный ключ (.pub). Нужен приватный — файл без .pub, он начинается с -----BEGIN'
}
# ssh refuses keys that other Windows users can read: leave access only to the current user.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $Key /inheritance:r /grant:r "*${sid}:(R)" | Out-Null

$target = "$User@$Server"
$sshOpts = @('-i', $Key, '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new',
             '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15')

# ------------------------------------------------------------------ connect
Step "Подключаюсь к $target"
& ssh.exe @sshOpts -o BatchMode=yes -p $Port $target 'true'
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Не получилось подключиться. Проверь:' -ForegroundColor Yellow
    Write-Host '  - IP правильный, сервер в статусе Running (первые 1-2 минуты после создания он ещё загружается);'
    Write-Host '  - выбран ключ, скачанный при создании ЭТОГО сервера;'
    Write-Host "  - пользователь: ubuntu для Ubuntu, opc для Oracle Linux (сейчас: $User)."
    Fail 'Нет подключения к серверу'
}
@{ server = $Server; key = $Key; user = $User; port = $Port } | ConvertTo-Json |
    Set-Content -LiteralPath $SavedFile -Encoding UTF8

if ($Logs) {
    Step 'Логи бота на сервере (выход — Ctrl+C)'
    & ssh.exe @sshOpts -t -p $Port $target "sudo journalctl -u $Service -n 50 -f"
    exit 0
}

# --------------------------------------------------------------------- pack
Step 'Упаковываю бота'
if (-not (Test-Path -LiteralPath '.env')) { Fail 'Нет файла .env с токеном бота' }
if (-not (Select-String -LiteralPath '.env' -Pattern '^\s*(BOT_TOKEN|BOT_API)\s*=\s*\S' -Quiet)) {
    Fail 'В .env нет токена (строка BOT_API=... или BOT_TOKEN=...)'
}
$bundle = Join-Path $env:TEMP $BundleName
Remove-Item -LiteralPath $bundle -ErrorAction SilentlyContinue
& tar.exe -czf $bundle --exclude=__pycache__ --exclude=*.pyc bot deploy/setup.sh main.py requirements.txt .env
if ($LASTEXITCODE -ne 0) { Fail 'Не удалось упаковать файлы' }

# ------------------------------------------------------------------- upload
Step 'Загружаю на сервер'
Push-Location -LiteralPath $env:TEMP   # relative path: scp would read "C:" as a host name
try {
    & scp.exe @sshOpts -q -P $Port $BundleName "${target}:/tmp/$BundleName"
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
    Remove-Item -LiteralPath $bundle -ErrorAction SilentlyContinue
}
if ($rc -ne 0) { Fail 'Не удалось загрузить файлы на сервер' }

# ------------------------------------------------------------------ install
Step 'Устанавливаю на сервере (в первый раз займёт 1-3 минуты)'
$remote = 'd=/tmp/footbalbot-deploy; rm -rf $d && mkdir -p $d && tar -xzf $d.tgz -C $d && ' +
          'sed -i ''s/\r$//'' $d/deploy/setup.sh && bash $d/deploy/setup.sh $d; ' +
          'rc=$?; rm -rf $d $d.tgz; exit $rc'
& ssh.exe @sshOpts -p $Port $target $remote
if ($LASTEXITCODE -ne 0) { Fail 'Установка не удалась — причина написана выше' }

Write-Host ''
Write-Host 'Бот работает на сервере. Открой его в Telegram и нажми /start.' -ForegroundColor Green
Write-Host 'Не запускай его одновременно на компьютере: Telegram разрешает только одну копию бота.'
Write-Host 'Обновить бота после изменений — снова запусти deploy.bat. Логи — deploy.bat -Logs'
