"""Autoteste das regras da v0.1.5. Rodar: python teste_regras.py (não mexe no servidor nem no banco real)."""
import tempfile, types
from pathlib import Path
import controle_jornada as cj

# 1) Dia de espera: movimento/parado do PDF não entram no cálculo (igual à planilha).
t, _ = cj.month_metrics([('2026-10-01', '', '', '00:14:06', '00:32:53', '10:00:00', 'ESPERA FIXA')])
assert (t['direcao'], t['intervalo'], t['descanso'], t['espera']) == (0, 0, 0, 36000), t
assert (t['he_total'], t['he_espera'], t['he_direcao']) == (7200, 7200, 0), t
# Dia normal (Excel agosto, 1º motorista, dia 2): HE zero, intervalo = jornada - mov - espera.
t, _ = cj.month_metrics([('2026-08-02', '08:22:30', '16:07:48', '01:16:35', '01:40:25', '00:00:00', 'JORNADA')])
assert t['intervalo'] == 27918 - 4595 and t['he_total'] == 0, t

# 2) Limpeza de nomes.
for raw, nome, tipo in [
    ('CARRETA BAU: JOAO TESTE LIMA (MR) (DEDICADOS) 111111', 'JOAO TESTE LIMA', 'CARRETA BAU'),
    ('RODOTREM BAU: PEDRO EXEMPLO SOUZA - {WG} 222222', 'PEDRO EXEMPLO SOUZA', 'RODOTREM BAU'),
    ('CARRETA BAU:MARCOS FICTICIO ROCHA-PS ( DEDICADOS) 333333', 'MARCOS FICTICIO ROCHA', 'CARRETA BAU'),
    ('RODOTREM SIDER: ANDRE MODELO CRUZ - LJ 444444', 'ANDRE MODELO CRUZ', 'RODOTREM SIDER'),
    ('CAMARA FRIA:CARLOS AMOSTRA DIAS', 'CARLOS AMOSTRA DIAS', 'CAMARA FRIA'),
]:
    assert cj.driver_parts(raw) == (nome, tipo), (raw, cj.driver_parts(raw))

# 3) Envio ao servidor: marca pendência se falhar, limpa se der certo, poda versões antigas.
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp); local = tmp / 'local'; server = tmp / 'server'
    local.mkdir(); (server / 'Backups').mkdir(parents=True)
    cj.LOCAL, cj.DB, cj.PENDING = local, local / 'jornadas.db', local / 'envio_pendente.flag'
    cj.init_db(cj.DB)
    label = types.SimpleNamespace(config=lambda **k: None)
    app = types.SimpleNamespace(server=str(tmp / 'nao_existe'), network=label)
    app.prune_versions = lambda p: cj.App.prune_versions(app, p)
    try:
        cj.App.publish(app); raise AssertionError('deveria falhar')
    except OSError:
        pass
    assert cj.PENDING.exists(), 'falha de rede precisa deixar pendência'
    for i in range(25):  # versões antigas de 2 dias
        (server / f'jornadas_2026100{1 + i % 2}_1200{i:02d}_000000.db').write_bytes(b'')
    app.server = str(server)
    cj.App.publish(app)
    assert not cj.PENDING.exists()
    assert len(list(server.glob('jornadas_*.db'))) == cj.KEEP_VERSIONS
    # As 6 mais antigas são do dia 01/10: só a mais nova delas fica guardada em Backups.
    assert [p.name for p in (server / 'Backups').glob('*.db')] == ['jornadas_20261001_120010_000000.db']
# 4) TXT do Domínio: mesmo layout do HUB RH (48 dígitos; 05:30 -> 530; empresa 82).
assert cj.txt_line('1612', '202611', cj.RUBRICA_ESPERA, 7200 + 59) == '10' + '0000001612' + '202611' + '000000571' + '11' + '000000200' + '0000000082'
emps = [{'codigo': '1001', 'nome': 'JOÃO TESTE LIMA'}, {'codigo': '1002', 'nome': 'PEDRO EXEMPLO SOUZA'}]
zero = dict.fromkeys(['he_direcao', 'he_espera'], 0)
lines, ok, pend = cj.payroll([
    ('AAA1A11', 'JOAO TESTE LIMA', {**zero, 'he_direcao': 3600}, [], ''),        # nome exato (sem acento)
    ('BBB2B22', 'P. EXEMPLO', {**zero, 'he_espera': 7200}, [], ''),              # nome cortado: pendência
    ('CCC3C33', 'P. EXEMPLO', {**zero, 'he_espera': 7200}, [], '1002'),          # código salvo: entra
    ('DDD4D44', 'JOAO TESTE LIMA', {**zero, 'he_direcao': 60}, ['negativo'], ''),  # dia negativo: fora
    ('EEE5E55', 'SEM HORA EXTRA', zero, [], ''),                                 # sem HE: ignorado
], emps, '202611')
assert [r['codigo'] for r in ok] == ['1001', '1002'] and len(lines) == 2, (ok, lines)
assert [p[0] for p in pend] == ['BBB2B22', 'DDD4D44'], pend
# 5) Motorista separado da placa: troca no meio do mês e nome cortado pelo rastreador.
with tempfile.TemporaryDirectory() as tmp:
    cj.DB = Path(tmp) / 'troca.db'; cj.init_db(cj.DB)
    linhas = [('XXX1A11', '2026-11-01', 'TRUCK: JOAO TESTE LIMA (WG) 111111'),
              ('XXX1A11', '2026-11-02', 'TRUCK: JOAO TESTE LIMA (WG) 111111'),
              ('YYY2B22', '2026-11-03', 'TRUCK: JOAO TESTE LIMA - LJ 222222'),       # A trocou para a placa Y
              ('XXX1A11', '2026-11-03', 'TRUCK: PEDRO EXEMPLO (WG) 111111'),         # B assumiu a placa X (nome cortado)
              ('XXX1A11', '2026-11-04', 'TRUCK: PEDRO EXEMPLO SOUZA (WG) 111111')]   # nome completo do mesmo B
    with cj.db() as con:
        for placa, data, nome in linhas:
            con.execute('INSERT INTO jornadas VALUES(?,?,?,?,?,?,?,?,?,?,?)', (placa, data, nome, '', '', '00:00:00', '00:00:00', '10:00:00', 'ESPERA FIXA', 'x', 'agora'))
        cj.link_drivers(con)
        nomes = dict(con.execute('SELECT nome,id FROM condutores'))
        assert set(nomes) == {'JOAO TESTE LIMA', 'PEDRO EXEMPLO SOUZA'}, nomes  # 2 motoristas, não 2 placas nem 3 nomes
        uso = cj.month_plates(con, '2026-11')
        assert [p for p, _, _ in uso[nomes['JOAO TESTE LIMA']]] == ['XXX1A11', 'YYY2B22'], uso
        assert [p for p, _, _ in uso[nomes['PEDRO EXEMPLO SOUZA']]] == ['XXX1A11'], uso
        dias = dict(con.execute(f'SELECT a.condutor,COUNT(*) {cj.JORNADAS} GROUP BY 1'))
        assert dias == {nomes['JOAO TESTE LIMA']: 3, nomes['PEDRO EXEMPLO SOUZA']: 2}, dias
        cj.link_drivers(con)  # rodar de novo não duplica
        assert con.execute('SELECT COUNT(*) FROM condutores').fetchone()[0] == 2
    con.close()  # Windows: banco aberto impede apagar a pasta temporária
# 6) Atualização automática: só release publicado, mais novo e com .exe; troca do arquivo.
exe = {'name': 'Controle_de_Jornada_AGT_LOG.exe', 'browser_download_url': 'u', 'size': 10, 'digest': 'sha256:ab'}
rel = lambda tag, **k: {'tag_name': tag, 'assets': [exe], 'draft': False, 'prerelease': False, **k}
assert cj.newer_release(rel('v0.1.8'), '0.1.7')['versao'] == '0.1.8'
assert cj.newer_release(rel('v0.1.10'), '0.1.9')['sha256'] == 'ab'      # 10 > 9 (não é comparação de texto)
assert cj.newer_release(rel('v0.1.7'), '0.1.7') is None                  # mesma versão
assert cj.newer_release(rel('v0.1.6'), '0.1.7') is None                  # mais antiga
assert cj.newer_release(rel('v0.2.0', prerelease=True), '0.1.7') is None
assert cj.newer_release({**rel('v0.2.0'), 'assets': []}, '0.1.7') is None  # sem .exe
with tempfile.TemporaryDirectory() as tmp:
    atual, novo = Path(tmp) / 'app.exe', Path(tmp) / 'app.novo.exe'
    atual.write_bytes(b'MZ velho'); novo.write_bytes(b'MZ novo')
    cj.install_update(novo, atual)
    assert atual.read_bytes() == b'MZ novo' and not novo.exists() and (Path(tmp) / 'app.antigo.exe').read_bytes() == b'MZ velho'
# 7) Espelho só de ida: servidor -> computador, com histórico de 1 cópia por dia.
import datetime as dt, sqlite3, os
with tempfile.TemporaryDirectory() as tmp:
    srv, esp = Path(tmp) / 'servidor', Path(tmp) / 'espelho'
    (srv / 'PDFs Originais').mkdir(parents=True); (srv / 'PDFs Originais' / 'a.pdf').write_bytes(b'%PDF')
    def versao(nome):
        cj.init_db(srv / nome)
    versao('jornadas_20251001_080000_000000.db')                            # mais de 1 ano antes de "hoje"
    versao('jornadas_20261105_090000_000000.db'); versao('jornadas_20261105_180000_000000.db')
    antes = {p.name: p.stat().st_mtime for p in srv.rglob('*')}
    hoje = dt.date(2026, 11, 6)
    cj.mirror_server(srv, esp, today=hoje)
    assert [p.name for p in esp.glob('*.db')] == ['jornadas_20261105_180000_000000.db'], list(esp.glob('*'))
    assert (esp / 'PDFs Originais' / 'a.pdf').exists()
    (srv / 'jornadas_20261106_100000_000000.db').write_bytes(b'corrompido')   # versão estragada: ignorada
    cj.mirror_server(srv, esp, today=hoje)
    assert len(list(esp.glob('*.db'))) == 1
    versao('jornadas_20261106_120000_000000.db'); cj.mirror_server(srv, esp, today=hoje)
    versao('jornadas_20261106_150000_000000.db'); cj.mirror_server(srv, esp, today=hoje)
    assert sorted(p.name for p in esp.glob('*.db')) == ['jornadas_20261105_180000_000000.db', 'jornadas_20261106_150000_000000.db']  # 1 por dia
    cj.mirror_server(srv, esp, today=dt.date(2027, 11, 6))                     # 1 ano depois: 05/11/2026 sai
    assert [p.name for p in esp.glob('*.db')] == ['jornadas_20261106_150000_000000.db']
    novos = {p.name: p.stat().st_mtime for p in srv.rglob('*')}
    assert all(novos[k] == v for k, v in antes.items()), 'o espelho não pode alterar o servidor'
print('OK - todas as regras conferidas')
