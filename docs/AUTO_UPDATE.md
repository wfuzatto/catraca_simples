# Atualização automática

O executável consulta este manifesto no início:

`https://prodatastelecom.com.br/sites/catraca_vale/latest.json`

Se `version` for maior que `APP_VERSION`, o app baixa o EXE, verifica tamanho e SHA-256, substitui o executável depois que o processo fecha e reabre a nova versão. Se a consulta ou a validação falhar, o cadastro abre normalmente com a versão instalada. A atualização funciona no EXE empacotado; executar `python app.py` não verifica atualizações.

## Publicação

O workflow **Compilar EXE Windows** testa o projeto, compila o EXE e publica uma GitHub Release `v<APP_VERSION>`. Para publicar no servidor Prodata:

1. Baixe o asset `ValeCadastroFacialNFC.exe` da Release.
2. Envie-o para `/sites/catraca_vale/ValeCadastroFacialNFC.exe`.
3. Calcule o tamanho em bytes e o SHA-256 desse arquivo.
4. Envie `latest.json` por último para `/sites/catraca_vale/latest.json`, no formato:

```json
{
  "version": "1.0.0-preview.84",
  "download_url": "https://prodatastelecom.com.br/sites/catraca_vale/ValeCadastroFacialNFC.exe",
  "size_bytes": 0,
  "sha256": "<64 caracteres hexadecimais do SHA-256>"
}
```

`size_bytes` e `sha256` devem ser preenchidos com os valores do EXE da preview.84. Use HTTPS no site. Publique o EXE antes do manifesto para que nenhum cliente leia uma versão nova enquanto o arquivo ainda estiver sendo transferido.

## Substituição local

O app baixa temporariamente o executável na mesma pasta da instalação. Um script PowerShell do Windows espera o processo encerrar, substitui o arquivo e inicia a nova versão. O app confirma a inicialização após construir a janela; sem essa confirmação, o script encerra a tentativa e restaura a versão anterior. Se a limpeza do backup falhar, a atualização concluída permanece ativa e o backup é mantido. A pasta precisa permitir gravação; para atualização sem elevação, use uma pasta gravável pelo usuário, como `%LOCALAPPDATA%\Programs\ValeCatracaSimples`.

O log fica em `%LOCALAPPDATA%\ValeCatracaSimples\updater.log`.

O primeiro EXE com suporte ao atualizador é a preview.81. Computadores com uma versão anterior precisam receber essa versão inicial uma vez; depois disso, recebem as versões seguintes pelo manifesto.
