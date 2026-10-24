# Homologação no hotel

- [ ] Confirmar host/porta da OpenAPI no servidor, versão do HikCentral e SAN do certificado.
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

O cadastro exige mapeamento explícito dos níveis Visitor por grupo. A prévia da
operação deve ser conferida antes do envio. Uma consulta OpenAPI bem-sucedida ou
uma resposta positiva de reaplicação, sozinha, não aprova a entrega física.

Nenhuma execução de CI hospedado consegue testar IP privado, webcam/ACR122U ou catraca física fora da rede do hotel.
