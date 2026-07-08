Add-Type -AssemblyName System.Drawing

function Encontrar-Imagem {
    param (
        [string]$BasePath,
        [string]$NomeBase
    )

    $extensoes = @("bmp","jpg","jpeg","png")

    foreach ($ext in $extensoes) {
        $arquivo = Join-Path $BasePath "$NomeBase.$ext"
        if (Test-Path $arquivo) {
            return $arquivo
        }
    }

    return $null
}

function Redimensionar-Imagem {
    param (
        [string]$Entrada,
        [string]$Saida,
        [int]$Largura,
        [int]$Altura
    )

    $img = [System.Drawing.Image]::FromFile($Entrada)

    $bmp = New-Object System.Drawing.Bitmap $Largura, $Altura
    $grafico = [System.Drawing.Graphics]::FromImage($bmp)
    $grafico.Clear([System.Drawing.Color]::White)

    # Mantém proporção
    $ratioX = $Largura / $img.Width
    $ratioY = $Altura / $img.Height
    $ratio = [Math]::Min($ratioX, $ratioY)

    $novaLargura = [int]($img.Width * $ratio)
    $novaAltura = [int]($img.Height * $ratio)

    $posX = [int](($Largura - $novaLargura) / 2)
    $posY = [int](($Altura - $novaAltura) / 2)

    $grafico.DrawImage($img, $posX, $posY, $novaLargura, $novaAltura)

    if ($Saida.ToLower().EndsWith(".jpg")) {
        $bmp.Save($Saida, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    } else {
        $bmp.Save($Saida, [System.Drawing.Imaging.ImageFormat]::Bmp)
    }

    $grafico.Dispose()
    $bmp.Dispose()
    $img.Dispose()
}

# Caminho base
$base = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Detecta automaticamente formatos
$logo1 = Encontrar-Imagem $base "logo1"
$logo2 = Encontrar-Imagem $base "logo2"

if (!$logo1) { Write-Host "logo1.(bmp/jpg/png) não encontrado"; pause; exit }
if (!$logo2) { Write-Host "logo2.(bmp/jpg/png) não encontrado"; pause; exit }

# Criar pasta SAIDA
$saida = Join-Path $base "SAIDA"

if (Test-Path $saida) {
    Remove-Item $saida -Recurse -Force
}

New-Item -ItemType Directory -Path $saida | Out-Null

# ===== GERAÇÃO DAS LOGOS =====

# Base logo1 (1x1)
Redimensionar-Imagem $logo1 (Join-Path $saida "logofrente.bmp") 350 350
Redimensionar-Imagem $logo1 (Join-Path $saida "logonfse.jpg") 100 100
Redimensionar-Imagem $logo1 (Join-Path $saida "logonfe.bmp") 530 340
Redimensionar-Imagem $logo1 (Join-Path $saida "logopaf.bmp") 332 278

# Base logo2 (1x1/4)
Redimensionar-Imagem $logo2 (Join-Path $saida "LOGOTIP.BMP") 360 90
Redimensionar-Imagem $logo2 (Join-Path $saida "logotip.jpg") 360 90

# ===== GERAR ZIP =====

$zipPath = Join-Path $base "LOGOS_FINAL.zip"

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path "$saida\*" -DestinationPath $zipPath

Write-Host ""
Write-Host "Logos geradas e compactadas com sucesso!"
Write-Host "Arquivo criado: LOGOS_FINAL.zip"

# ===== William Code =====