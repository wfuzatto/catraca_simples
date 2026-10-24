# Homologação no hotel

- [ ] Identificar porta real da OpenAPI Gateway e versão do HikCentral no IP 192.168.100.102.
- [ ] Criar AppKey/AppSecret de partner com permissões específicas para Visitor e ACS.
- [ ] Verificar certificado TLS e nome/IP presente em SAN, sem desativar verificação.
- [ ] Sincronizar e conferir grupos reais: Day use, Hóspedes Serra, Hóspedes Vale, Visitors.
- [ ] Validar os quatro níveis: CATRACAS SABIA, ENTRADA ACQUAVALE, LOJA ACQUAVALE, SAIDA ACQUAVALE.
- [ ] Validar UID da pulseira pelo ACR122U versus valor lido nativamente pelo HikCentral. Se necessário, ajustar ordem de bytes.
- [ ] Capturar foto webcam; conferir qualidade e aceitação facial nas catracas.
- [ ] Cadastrar visitante de teste com ingressos 1/2/3 dias e comparar vencimento.
- [ ] Confirmar remark com número opcional da reserva.
- [ ] Confirmar visitorId, grupo, foto, cartão e níveis no painel HikCentral.
- [ ] Confirmar distribuição efetiva da credencial nos terminais e teste de passagem.
- [ ] Simular falha de rede após registro; botão Concluir pendente deve evitar criação duplicada.
- [ ] Confirmar procedimentos de exclusão/expiração de biometria e políticas LGPD.

Nenhuma execução de CI hospedado consegue testar IP privado, webcam/ACR122U ou catraca física fora da rede do hotel.
