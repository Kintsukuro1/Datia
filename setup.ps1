# ============================================================================
#  Dat.ia - configuracion automatica para otra maquina (Windows)
#
#  No ejecutar por doble clic directo a este archivo: usa setup.bat.
#
#  Que hace:
#    1. Verifica que Docker este instalado y encendido.
#    2. Crea el .env desde .env.example (si no existe todavia).
#    3. Genera los tres valores que no pueden quedar en default:
#       FERNET_KEY, SECRET_KEY y POSTGRES_PASSWORD.
#    4. Arranca los contenedores y abre http://localhost.
#
#  Idempotente: re-ejecutarlo no pisa valores ya configurados.
#
#  Solo ASCII a proposito: las consolas cmd con codepage 850 convierten los
#  acentos en mojibake y este script lo lee gente no tecnica.
# ============================================================================

trap {
    Write-Host ""
    Write-Host "  [X] Error inesperado: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    exit 1
}

$dir     = $PSScriptRoot
$envPath = Join-Path $dir ".env"
$example = Join-Path $dir ".env.example"
$compose = Join-Path $dir "docker-compose.partner.yml"

Set-Location -LiteralPath $dir

function Fail($msg) {
    Write-Host ""
    Write-Host "  [X] $msg" -ForegroundColor Red
    Write-Host ""
    exit 1
}
function Ok($msg)    { Write-Host "  [OK] $msg" }
function Doing($msg) { Write-Host "  [..] $msg" }

Write-Host ""
Write-Host "  ============================================"
Write-Host "   Dat.ia - configuracion automatica"
Write-Host "  ============================================"
Write-Host ""

# --- 1. Docker instalado y encendido ----------------------------------------

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker no esta instalado.`n      Instala Docker Desktop y volve a ejecutar este archivo:`n      https://www.docker.com/products/docker-desktop/"
}

Doing "Verificando Docker..."
cmd /c "docker info >NUL 2>&1"
if ($LASTEXITCODE -ne 0) {
    Fail "Docker esta instalado pero el motor no esta encendido.`n      Abri Docker Desktop, espera a que diga 'Engine running'`n      y volve a ejecutar setup.bat."
}
Ok "Docker encendido."

# --- 2. .env ----------------------------------------------------------------

if (-not (Test-Path $example)) {
    Fail "No se encontro .env.example junto a este script.`n      Descarga la carpeta completa del repositorio, no solo el setup.bat."
}

if (-not (Test-Path $envPath)) {
    Copy-Item $example $envPath
    Ok ".env creado a partir de .env.example."
} else {
    Doing ".env ya existia: se completan solo los valores vacios o en default."
}

# --- 3. Generar los valores que no pueden quedar en default -----------------

function New-Hex([int]$nBytes) {
    $buf = [byte[]]::new($nBytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    return (($buf | ForEach-Object { $_.ToString("x2") }) -join "")
}

function New-FernetKey {
    $buf = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    return [Convert]::ToBase64String($buf).Replace("+", "-").Replace("/", "_")
}

$defaults = @{
    "FERNET_KEY"        = ""
    "SECRET_KEY"        = "democratizacion_datos_super_secret_key_local_2026_aes256_change_in_prod"
    "POSTGRES_PASSWORD" = "postgres"
}

$text = [System.IO.File]::ReadAllText($envPath)
$newPassword = $false

foreach ($key in @("FERNET_KEY", "SECRET_KEY", "POSTGRES_PASSWORD")) {
    $find = [regex]::Match($text, "(?m)^" + $key + "=([^\r\n]*)")
    $current = ""
    if ($find.Success) { $current = $find.Groups[1].Value.Trim() }

    if ($find.Success -and $current -ne "" -and $current -ne $defaults[$key]) {
        Doing "$key ya estaba configurado: se deja como esta."
        continue
    }

    $value = switch ($key) {
        "FERNET_KEY"        { New-FernetKey }
        "SECRET_KEY"        { New-Hex 32 }
        "POSTGRES_PASSWORD" { New-Hex 12 }
    }

    if ($find.Success) {
        $text = $text -replace ("(?m)^" + $key + "=[^\r\n]*"), ($key + "=" + $value)
    } else {
        $text = $text.TrimEnd() + "`r`n" + $key + "=" + $value + "`r`n"
    }
    Ok "$key generado."
    if ($key -eq "POSTGRES_PASSWORD") { $newPassword = $true }
}

[System.IO.File]::WriteAllText($envPath, $text, (New-Object System.Text.UTF8Encoding($false)))

# --- 4. Aviso: instalacion previa con contrasena distinta -------------------

if ($newPassword) {
    $old = @(docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -match "postgres_data" })
    if ($old.Count -gt 0) {
        Write-Host ""
        Write-Host "  [!] Hay una instalacion anterior de Dat.ia en este Docker (volumen" -ForegroundColor Yellow
        Write-Host "      de Postgres existente) y se genero una contrasena nueva." -ForegroundColor Yellow
        Write-Host "      Si el arranque falla con un error de contrasena, corre en esta carpeta:" -ForegroundColor Yellow
        Write-Host "          docker compose -f docker-compose.partner.yml down -v" -ForegroundColor Yellow
        Write-Host "      Eso borra la base de Dat.ia y la recrea limpia." -ForegroundColor Yellow
    }
}

# --- 5. Arrancar -------------------------------------------------------------

Write-Host ""
Doing "Bajando imagenes y arrancando (la primera vez baja ~310 MB, puede tardar unos minutos)..."
& docker compose -f $compose up -d --wait --wait-timeout 420

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  [X] Algo fallo al arrancar. Ultimas lineas del backend:" -ForegroundColor Red
    Write-Host ""
    & docker compose -f $compose logs --tail 25 backend
    Write-Host ""
    Write-Host "  Guia de problemas frecuentes: PARTE-DOCKER.md (seccion 4)."
    Write-Host ""
    exit 1
}

# El backend puede arrancar "healthy" sin estar contra el Postgres real si la
# imagen es vieja: las instalaciones anteriores traian un fallback silencioso a
# SQLite que el codigo actual ya NO tiene (database.py falla ruidoso en el
# import). `docker compose up` reutiliza la imagen local si existe, asi que se
# verifica el marcador del motor real en los logs.
$usesPg = @(docker compose -f $compose logs backend 2>$null | Select-String "Motor de base de datos activo: POSTGRESQL")
if ($usesPg.Count -eq 0) {
    Write-Host ""
    Write-Host "  [X] El backend arranco pero NO esta usando PostgreSQL." -ForegroundColor Red
    Write-Host "      Ultimas lineas del backend:" -ForegroundColor Red
    Write-Host ""
    & docker compose -f $compose logs --tail 25 backend
    Write-Host ""
    Write-Host "  Si esto es una instalacion anterior de Dat.ia, corre en esta carpeta:" -ForegroundColor Red
    Write-Host "      docker compose -f docker-compose.partner.yml down -v" -ForegroundColor Red
    Write-Host "  (borra la base de Dat.ia y la recrea limpia) y volve a ejecutar setup.bat." -ForegroundColor Red
    Write-Host ""
    exit 1
}

# --- 6. Listo ----------------------------------------------------------------

Write-Host ""
Ok "Dat.ia esta corriendo."
Write-Host ""
Doing "Abriendo http://localhost en el navegador..."
Start-Process "http://localhost"
Write-Host ""
Write-Host "  Usuarios demo:"
Write-Host "    admin       admin123       (acceso total)"
Write-Host "    director    director123"
Write-Host "    economista  economista123"
Write-Host "    bi          bi123"
Write-Host ""
Write-Host "  La IA del chat es opcional y corre en esta maquina, aparte:"
Write-Host "      ollama serve"
Write-Host "      ollama pull qwen2.5-coder:7b"
Write-Host "  Sin Ollama, el chat avisa 'IA local no disponible' y el resto funciona igual."
Write-Host ""
Write-Host "  Para parar:   docker compose -f docker-compose.partner.yml down"
Write-Host ""
exit 0
