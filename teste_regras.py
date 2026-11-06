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
print('OK - todas as regras conferidas')
