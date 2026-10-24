# Vale - Cadastro Facial e NFC (Windows)

Aplicativo Windows para check-in de visitantes diretamente no HikCentral Professional na rede interna. Sem sincronização online/offline e sem servidor intermediário.

## Operação
- Nome, sobrenome e grupos Visitor reais obtidos no HikCentral.
- Foto via webcam ou arquivo, enviada via Base64 JPEG.
- Pulseira NFC por ACR122U/PC-SC, com conversão configurável (HEX, decimal big-endian, decimal little-endian) e número editável.
- Ingressos de 1, 2 ou 3 dias, personalizáveis. Por padrão a saída é às 23:59:59 do último dia incluído.
- Entrada e saída editáveis, com fuso America/Sao_Paulo.
- Reserva opcional no campo VisitorInfo.remark.
- Associa níveis de acesso por grupo e solicita reaplicação das permissões.
- Recurso Concluir pendente retoma distribuição sem criar novo visitante caso falhe após criação.

Grupos iniciais conforme as imagens: Day use, Hóspedes Serra, Hóspedes Vale, Visitors. A lista real sempre é consultada antes da operação.

Níveis iniciais conforme as imagens: CATRACAS SABIA, ENTRADA ACQUAVALE, LOJA ACQUAVALE, SAIDA ACQUAVALE. Edite o mapeamento em Configurações conforme a política de acesso do hotel.

## OpenAPI (não usa login Web)
1. POST /artemis/api/visitor/v1/visitorgroups: recuperar grupos.
2. POST /artemis/api/acs/v1/privilege/group: consultar níveis.
3. POST /artemis/api/visitor/v1/registerment: registrar foto, cartão, grupo, período e remark.
4. POST /artemis/api/acs/v1/privilege/group/single/addPersons: atribuir níveis ao visitorId.
5. POST /artemis/api/visitor/v1/auth/reapplication: solicitar envio às catracas.

## Instalação / compilação
No GitHub, entre em Actions > Compilar EXE Windows e faça download do artefato ValeCadastroFacialNFC-Windows depois de execução bem-sucedida. Ele contém ValeCadastroFacialNFC.exe compilado pelo PyInstaller com runtime Python embutido.

Requisitos externos: Windows 10/11, rede até o HikCentral e drivers PC/SC do leitor ACR122U (para leitura NFC). Câmera configurada no Windows.

Para executar pelo código: Python 3.12, pip install -r requirements.txt, python app.py.
Testes: python -m pytest -q tests.

## Primeira configuração
1. No HikCentral Web Client, habilite o OpenAPI Gateway e crie um parceiro (AppKey/AppSecret) com permissões de Visitor e ACS. A conta admin do Web Client não funciona como AppKey.
2. Abra Configurações. IP padrão: 192.168.100.102. Porta 37443 é sugestão do ambiente anterior e precisa ser conferida na instalação.
3. Configure AppKey, AppSecret e, se necessário, caminho da CA PEM do certificado HTTPS. O aplicativo NÃO desativa a validação TLS/hostname. Para acesso por IP, o certificado precisa ter IP em SAN, ou utilize nome DNS correspondente.
4. Selecione conversão UID e valide o número real com um cartão no HikCentral. O UID do ACR122U não é necessariamente igual à credencial configurada na catraca.
5. Ajuste níveis por grupo e horários de ingressos; Salve, Sincronize e faça um cadastro de teste.

## Segurança e persistência
Configurações ficam em %LOCALAPPDATA%\ValeCatracaSimples\settings.json; as chaves OpenAPI ficam em secrets.dpapi protegidas por DPAPI do usuário Windows. IDs de cadastros pendentes ficam em pending.json, sem fotos nem nome do hóspede. Fotos não são gravadas no disco pela aplicação. Não inclua credenciais em arquivos do GitHub.

A senha admin compartilhada no planejamento NÃO é incorporada ao aplicativo e deve ser trocada. O uso de dados faciais depende de política de privacidade, base legal e segurança adequadas à LGPD.

## Limites da entrega
O EXE pode ser compilado no GitHub, mas o servidor privado 192.168.100.102, leitores e catracas precisam de homologação presencial. Resposta positiva na solicitação de reaplicação não comprova entrega a todos os dispositivos. Verifique status no HikCentral.

Referência: https://enpinfo.hikvision.com/hkwsen/unzip/20260723200832_95640_doc/index.html

## Certificado privado do HikCentral (localhost:443)

O Vale Visitor PHP anterior podia operar com TLS_VERIFY=0. Para evitar o envio
de credenciais e fotos para um serviço não autenticado, o aplicativo Windows
aceita duas alternativas de verificação:

1. Usar DNS correto no certificado + CA PEM confiável (opção preferencial).
2. Ler e conferir a impressão digital SHA-256 do certificado instalado no
   HikCentral, confirmando-a fora do aplicativo antes de aprová-la.

Na opção 2, o certificado TLS deve coincidir exatamente com o SHA-256 aprovado
ANTES de transmitir AppKey, AppSecret, nome, foto ou credencial NFC.
Qualquer substituição de certificado bloqueia a transmissão automaticamente.

A impressão deve ser comparada com um certificado obtido por canal confiável
no servidor; não confie nela só porque foi mostrada em conexão não validada.
Este modo não valida CA, hostname nem validade: use somente em ambiente interno
autorizado e servidor cuja identidade foi confirmada.

Use Diagnóstico de rede para saber se TCP 443/37443 está acessível. Ao testar
a conexão, o aplicativo salva as alterações digitadas antes da chamada OpenAPI.
Não exponha senhas/chaves em prints nem no repositório GitHub.

