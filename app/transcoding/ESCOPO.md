# Escopo de Trabalho — Módulo de Transcodificação

> Documento de referência rápida: quem é o responsável, onde o código vive e
> quais regras de escopo valem para este módulo. (Fonte: `JOAO-VITOR-ESCOPO.txt`)

## Responsável

**João Vitor**

## Repositório oficial

<https://github.com/Pietro2820/projeto-a3-Garantia-da-Qualidade-de-Software-2026.2>

Convite do GitHub: ✅ **aceito**.

## Branch de trabalho

```
feature/transcodificacao
```

## Regra de escopo

- Trabalhar **somente** na branch `feature/transcodificacao`.
- **Não alterar diretamente** `main`, `develop` ou branches dos outros integrantes.
- Mudanças entram em `develop` **apenas via Pull Request** (regra #2 do guia de
  sobrevivência do Git), com commits semânticos (`feat:`, `fix:`, `test:`, `docs:`...).

## Módulo sob responsabilidade

- Transcodificação **FFmpeg** (conversão multirresolução, HLS `.m3u8` + `.ts`,
  master playlist, thumbnail)
- Integração com **AWS S3 / MinIO** (armazenamento dos vídeos original e
  processados — critério de "integração com serviço externo" do edital)

**Fora do escopo deste módulo**: metadados no Supabase (Pietro/Rafael), fila
Celery (Pietro — este módulo só fornece a função do contrato, ver
`CONTRATO_TASK.md`), player (Pedro), recomendações (Gustavo).

## Documentos relacionados

| Arquivo | Para quê |
|---|---|
| `README.md` | manual de operação do módulo (setup, testes, S3/MinIO) |
| `CONTRATO_TASK.md` | contrato de entrada/saída da task Celery (levar pro Pietro) |
| `../upload/` (Rafael) | de onde vem o original: `uploads/{video_id}/original.{ext}` |
