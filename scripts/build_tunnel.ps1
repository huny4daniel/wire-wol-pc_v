# WireGuard 내장 터널에 필요한 세 파일을 vendor\amd64\에 만든다.
#   - tunnel.dll        : wireguard-windows의 embeddable-dll-service를 직접 빌드
#   - wireguard.dll     : 공식 wireguard-nt 배포본(커널 드라이버 포함)
#   - wirewol-tunnel-host.exe : tunnel_host\tunnel_host.c(서비스 호스트)
#
# Go/wireguard-nt 다운로드 주소와 SHA256은 지정한 wireguard-windows 태그의
# build.bat에서 그대로 읽어온다 — 원본 빌드와 같은 툴체인 버전을 쓰기 위함
# (특히 .overlay가 특정 Go 버전의 표준 라이브러리 경로를 덮어쓰므로 버전이
# 어긋나면 빌드가 깨진다). cgo용 gcc(mingw-w64)는 PATH에 있어야 한다.
#
# 사용법(PowerShell 7): pwsh -File scripts\build_tunnel.ps1 [-Ref v1.1.1]
param(
    [string]$Ref = 'v1.1.1'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$projectDir = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $projectDir 'vendor\amd64'
$workDir = Join-Path $env:TEMP "wirewolpc-tunnel-build\$Ref"
$repoDir = Join-Path $workDir 'wireguard-windows'
$depsDir = Join-Path $repoDir '.deps'

New-Item -ItemType Directory -Force $workDir, $outDir | Out-Null

if (-not (Test-Path $repoDir)) {
    git clone --depth 1 --branch $Ref https://github.com/WireGuard/wireguard-windows.git $repoDir
    if ($LASTEXITCODE -ne 0) { throw 'wireguard-windows 소스를 받지 못했습니다' }
}

$buildBat = Get-Content (Join-Path $repoDir 'build.bat') -Raw

function Get-Dependency([string]$name, [string]$checkFile) {
    $m = [regex]::Match($buildBat, "call :download $([regex]::Escape($name)) (\S+) ([0-9a-f]{64})")
    if (-not $m.Success) { throw "build.bat에서 $name 정보를 찾지 못했습니다" }
    $url = $m.Groups[1].Value
    $sha = $m.Groups[2].Value
    if (Test-Path (Join-Path $depsDir $checkFile)) { return }
    New-Item -ItemType Directory -Force $depsDir | Out-Null
    $zip = Join-Path $depsDir $name
    Write-Host "[+] $url"
    Invoke-WebRequest -Uri $url -OutFile $zip
    $actual = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($actual -ne $sha) { throw "$name 해시 불일치: $actual" }
    # Git Bash가 PATH에 있으면 GNU tar가 먼저 잡혀 "C:" 경로를 원격 호스트로
    # 해석해버린다 — 윈도우 기본 tar(bsdtar)를 직접 지정한다.
    & "$env:SystemRoot\System32\tar.exe" -xf $zip -C $depsDir
    if ($LASTEXITCODE -ne 0) { throw "$name 압축 해제 실패" }
    Remove-Item $zip
}

Get-Dependency 'go.zip' 'go\bin\go.exe'
Get-Dependency 'wireguard-nt.zip' 'wireguard-nt\bin\amd64\wireguard.dll'

$env:PATH = "$depsDir\go\bin;$env:PATH"
$env:GOROOT = "$depsDir\go"
$env:GOPATH = "$depsDir\gopath"
$env:GOOS = 'windows'
$env:GOARCH = 'amd64'
$env:CGO_ENABLED = '1'
$env:CC = 'gcc'
$env:CGO_CFLAGS = '-O3 -Wall -Wno-unused-function -Wno-switch -std=gnu11 -DWINVER=0x0A00'

Write-Host '[+] tunnel.dll 빌드'
$tunnelOut = Join-Path $workDir 'tunnel.dll'
# 최신 master는 Go 표준 라이브러리 일부를 .overlay로 덮어써서 빌드하지만,
# 릴리즈 태그(v1.1.1 등)에는 오버레이가 없다 — 해당 태그의 build.bat과 같게 맞춘다.
if (Test-Path (Join-Path $repoDir '.overlay\overlay.json')) {
    go -C $repoDir build -overlay .overlay/overlay.json -buildmode c-shared -ldflags='-w -s' -trimpath -o $tunnelOut ./embeddable-dll-service
} else {
    go -C (Join-Path $repoDir 'embeddable-dll-service') build -buildmode c-shared -ldflags='-w -s' -trimpath -o $tunnelOut
}
if ($LASTEXITCODE -ne 0) { throw 'tunnel.dll 빌드 실패' }

Write-Host '[+] 서비스 호스트 빌드'
$hostOut = Join-Path $workDir 'wirewol-tunnel-host.exe'
gcc -O2 -municode -static -s -o $hostOut (Join-Path $projectDir 'tunnel_host\tunnel_host.c')
if ($LASTEXITCODE -ne 0) { throw '서비스 호스트 빌드 실패' }

Copy-Item $tunnelOut, $hostOut, (Join-Path $depsDir 'wireguard-nt\bin\amd64\wireguard.dll') $outDir -Force
Write-Host "[+] 완료: $outDir"
Get-ChildItem $outDir | Format-Table Name, Length
