# Reparo de base FDB

Este modulo repara bases Firebird do Small.

## Uso no Linux pela aplicacao web

1. Abra o modulo "Reparo de Base Firebird" no React.
2. Envie um arquivo `.fdb` por drag and drop ou pelo seletor.
3. Confirme o aviso de risco.
4. O backend cria backup, trabalha em copia isolada e executa `gfix`/`gbak`.
5. Ao final, baixe a base reparada e consulte o relatorio/log.

Configure no `.env`:

```env
GFIX_BIN=gfix
GBAK_BIN=gbak
FIREBIRD_USER=SYSDBA
FIREBIRD_PASSWORD=masterkey
```

O arquivo original enviado nunca e alterado diretamente.

## Referencia Windows

O `start.bat` foi mantido como referencia historica para uso local no Windows. Ele nao e usado pelo servidor Linux.
