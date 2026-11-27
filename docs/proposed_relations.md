# Предложенные связи по крупным карточкам

Это черновик для чтения, не справочник поиска. Код и `taxonomy/relations.json` не менялись.

Выборка из снимка `catalog-f62e28077667b7d564b7`. Продаж в фиде нет. Для софта «крупная карточка» — много офферов-лицензий. Для электроники карточка почти всегда равна одному офферу, поэтому взяты узнаваемые линейки, а не сотня одинаковых ИБП. Полный каталог не просматривался. Для серверов, ноутбуков, Windows, Microsoft 365, VMware vSphere и AnyDesk ниже заданы правила на все карточки роли в этом снимке. Совместимость конкретных моделей не утверждается.

## Около ста наименований

### Электроника

| Линейка | Примеры карточек |
|---|---|
| Серверы | INFERIT RS212, RS108, RS110 (`offer:542026` и соседние); Rack-сервер DEPO Storm 3470 |
| ПК | ПК INFERIT Micro |
| Ноутбуки | IRU Office С1502; HP 15-bs156ur; HP ProBook 445R G6 |
| Видеокарты | MSI и ASUS GeForce GT 1030; Gigabyte GeForce RTX 3070, RTX 3080, RTX 3090 |
| ИБП | APC Smart-UPS C 3000VA; Ippon Innova RT II; CyberPower; Powercom |
| NAS | Synology RackStation RS1221+; QNAP TS-1232PXU, QNAP D2 |
| Диски | Seagate Barracuda, Seagate Exos; Synology HAT5300; Toshiba HDD SAS |
| Сеть | Коммутаторы TP-Link, D-Link DGS, MikroTik CRS; точки доступа TP-Link EAP |
| Печать | Принтер Kyocera Ecosys P2235dn; МФУ Kyocera Ecosys |
| Экран и связь | Мониторы AOC и BenQ; IP-телефон Yealink; гарнитура Logitech |

### Софт

| Назначение | Примеры карточек |
|---|---|
| ОС для рабочих мест | Astra Linux Special Edition `group:121231`; Альт Рабочая станция 11; Альт СП 10; РЕД ОС; Windows 11 Pro; Windows 11 Home; Windows 10 Enterprise |
| Серверные ОС | Альт Сервер 11; Windows Server Essentials 2019; Windows Server CAL 2019; Red Hat Enterprise Linux Server |
| Офис | Microsoft 365 Family и Personal; Office 2024 для дома и учёбы; Office 2024 для дома и бизнеса; Word, PowerPoint, Access, Publisher 2021; Р7-Офис; МойОфис Стандартный 3 |
| Антивирус и защита устройств | Dr.Web Desktop Security Suite; Dr.Web Security Space; PRO32 Endpoint Security Advanced и Standard; Kaspersky Endpoint Security Cloud; Kaspersky для бизнеса Стандартный, Расширенный и Total; Kaspersky Small Office Security; Kaspersky Standard; MaxPatrol Endpoint Protection; F-Secure Elements; Symantec Endpoint Protection |
| Защита рядом с офисом и виртуализацией | Kaspersky Security для Microsoft Office 365; Kaspersky Security для виртуальных и облачных сред |
| Резервное копирование | Кибер Бэкап Старт, Стандартная, Расширенная, Облачный; Хайстекс Акура; Altaro VM Backup; Bacula Enterprise; Arcserve Backup |
| Виртуализация | VMware vSphere 7; «Альт Виртуализация» 11 PVE |
| Удалённый доступ | AnyDesk; TeamViewer; Фаматек Radmin; RMS Удаленный доступ; АССИСТЕНТ |
| Криптография и контроль | КриптоПро CSP 5.0; КриптоПро NGate; КриптоПро Ключ; JaCarta; С-Терра Шлюз; Эшелон «СКАНЕР-ВС» |
| Каталоги учётных записей | Dynamic Directory; Microsoft Azure Active Directory |
| Учёт и мониторинг, без очевидной пары в этой выборке | Directum RX; Zoho ManageEngine OpManager; ПК Гранд-СМЕТА |

## Электроника и очевидный софт

| От линейки | К каким карточкам | Почему это очевидно |
|---|---|---|
| Сервер INFERIT, DEPO Storm | Альт Сервер, Windows Server, Red Hat; Альт Виртуализация, VMware vSphere; Кибер Бэкап, Хайстекс Акура, Altaro, Arcserve | На сервере нужна ОС, часто гипервизор и резервные копии. Astra, РЕД ОС и Альт СП тоже могут стоять на сервере, но в названиях карточек это не сказано |
| Ноутбук, офисный ПК INFERIT | Windows 11, Альт Рабочая станция, Astra Linux, РЕД ОС; Microsoft 365, Office 2024, Р7-Офис, МойОфис; Dr.Web, Kaspersky Small Office, PRO32 | Рабочее место без ОС, офисного пакета и антивируса не собирается |
| NAS Synology, QNAP | Кибер Бэкап, Bacula, Arcserve, Хайстекс Акура | Сетевое хранилище — место для резервных копий. Это не лицензия самой прошивки NAS |
| Коммутатор и точка доступа | Zoho ManageEngine OpManager | Только как программа наблюдения за сетью. Для домашнего TP-Link это слабая пара |

Для ИБП, диска, монитора, принтера, телефона, гарнитуры и видеокарты в этой сотне нет очевидной программной карточки. Видеокарте не подбирался «софт для GPU»: в выборке нет отдельного продукта под драйвер или CUDA.

## Софт и софт

| От карточек | К каким карточкам | Почему это очевидно |
|---|---|---|
| Microsoft 365, Office 2024, Word, Р7-Офис, МойОфис | Dr.Web, Kaspersky Small Office, Kaspersky для бизнеса, PRO32. Для Microsoft 365 ещё Kaspersky Security для Microsoft Office 365 | Офисный пакет не заменяет антивирус. Отдельная карточка Kaspersky для Office 365 прямо называет этот контур |
| Windows 11, Windows 10, Альт Рабочая станция, Astra Linux, РЕД ОС | Те же антивирусы; Кибер Бэкап Старт или Стандартная | На рабочей ОС обычно нужны защита и копии файлов. Сертификат антивируса под конкретную ОС здесь не проверялся |
| Альт Сервер, Windows Server, Red Hat | Кибер Бэкап Расширенная, Хайстекс Акура, Arcserve; Альт Виртуализация или VMware vSphere; Dynamic Directory или Azure Active Directory | Серверная ОС дополняется копиями, виртуальными машинами и службой учётных записей |
| VMware vSphere, Альт Виртуализация | Кибер Бэкап, Altaro VM Backup, Хайстекс Акура; Kaspersky Security для виртуальных и облачных сред | У этих карточек в названии или классе уже есть виртуальные машины |
| AnyDesk, TeamViewer, Radmin, RMS, АССИСТЕНТ | Dr.Web, Kaspersky Endpoint, PRO32, MaxPatrol | Удалённый вход расширяет поверхность атаки, его закрывают защитой устройства, а не вторым клиентом удалённого доступа |
| КриптоПро CSP | Р7-Офис, МойОфис, Microsoft Office | Подпись и шифрование документов рядом с офисом. Это уже не антивирус, пара уместна для юрлица |

Не предлагались пары вроде «антивирус к антивирусу», «офис к другому офису» и «КриптоПро к игровой видеокарте».

## Соседние карточки с тем же предложением

Ниже товары того же назначения, что уже есть в таблицах. Для них не подбирался новый набор: берётся предложение исходной строки. Внутри одной ячейки по-прежнему примеры одной роли, а не несколько обязательных покупок.

| Если нашли | Рядом в каталоге | Какое предложение повторить |
|---|---|---|
| Сервер INFERIT, DEPO Storm 3470 | Другие корпуса DEPO Storm, микросервер DEPO Stream, Supermicro SuperServer | Серверная ОС, виртуализация, резервное копирование |
| Ноутбук IRU, HP | Acer Aspire и Extensa, ASUS ExpertBook, VivoBook и Zenbook, Dell Pro, Gigabyte, Aquarius | ОС рабочего места, офис, антивирус |
| NAS Synology, QNAP TS-1232 и D2 | ASUSTOR, ASUS NAS, QNAP TS-133, TS-233, TS-264, TS-453, TS-464, TS-873 | Резервное копирование |
| Windows 11, Альт Рабочая станция, Astra, РЕД ОС | Альт Образование 11, Windows 10 Enterprise CSP, Linux Mint, Debian | Антивирус и Кибер Бэкап Старт или Стандартная |
| Альт Сервер, Windows Server Essentials, Red Hat | Windows Server Datacenter 2022, Essentials 2022 и CAL 2022 | Резервное копирование, виртуализация, служба каталогов. CAL сама по себе не сервер, к ней этот набор не вешать |
| Microsoft 365, Office 2024, Word, Р7-Офис, МойОфис | Office Professional Plus 2021, Office Standard 2019, Excel, Outlook, Microsoft 365 Business Basic, Р7-Офис для вузов | Те же антивирусы. Для облачного Microsoft ещё Kaspersky Security для Office 365 |
| Dr.Web, PRO32, Kaspersky Endpoint и Small Office | Norman Endpoint Protection, Kaspersky Plus и Premium, подписка Small Office, редакция «Расширенный Плюс» | Их можно ставить в ту же роль антивируса, а не предлагать друг к другу |
| Кибер Бэкап, Хайстекс, Altaro, Bacula, Arcserve | Других крупных карточек резервного копирования в этой выборке нет | Их можно ставить в ту же роль копий |
| VMware vSphere, Альт Виртуализация | VMware Essentials, Standard, Enterprise Plus, Remote Office и vCenter Server 7 | Копии виртуальных машин и Kaspersky для виртуальных сред. Workstation и Fusion в этот набор не входят |
| AnyDesk, TeamViewer, Radmin, RMS, АССИСТЕНТ | «10-Страйк: Удаленный Доступ», TeamViewer Remote Management и Pilot | Защита устройства. Клиентские лицензии Windows Remote Desktop Services сюда не входят: это часть Windows Server |

## Правила на все карточки роли

Таблицы выше называют примеры. Здесь то же предложение распространяется на каждую карточку роли в снимке. Отдельная связь на корпус, редакцию или лицензию не заводится.

Карточка попадает под правило по классу или по тому, чем она является, а не по любому слову в названии. В столбце «что предложить» по-прежнему примеры одной роли: покупателю достаточно одного названия из ячейки. У сервера таких ролей три, у ноутбука три, у настольной Windows две, у vSphere две, у Microsoft 365 две, у AnyDesk одна.

Код и `taxonomy/relations.json` этим разделом не меняются.

| Если нашли | Какие карточки снимка входят | Что предложить | Что в это правило не входит |
|---|---|---|---|
| Сервер | 23 карточки. Класс `server_hardware`: 7 конфигураций INFERIT RS108, RS110, RS212. Вне класса: 10 конфигураций DEPO Storm 3470, микросервер DEPO Stream 1040C2X, Supermicro SuperServer 621P-TR и 621P-TRT, 3 серверные платформы | Серверная ОС: Альт Сервер, Windows Server, Red Hat. Виртуализация: Альт Виртуализация, VMware vSphere. Резервное копирование: Кибер Бэкап, Хайстекс Акура, Altaro, Arcserve | Радиатор процессора для сервера и стойка DEPO 42U. Это не вычислительная машина |
| Ноутбук | Все 49 карточек класса `laptop`: Lenovo 23, ASUS 8, HP 6, Acer 3, Gigabyte 3, и по одной IRU, Dell, Aquarius, MSI, Гравитон, TECNO | ОС рабочего места: Windows 11, Альт Рабочая станция, Astra Linux, РЕД ОС. Офис: Microsoft 365, Office 2024, Р7-Офис, МойОфис. Антивирус: Dr.Web, Kaspersky Small Office, PRO32 | 29 карточек со словом «ноутбук» вне класса: сумки, мыши, подставки, стол, адаптер, камера, числовой блок |
| Настольная Windows | 5 карточек, где сам продукт — клиентская ОС: Windows 11 Pro, Windows 11 Home, Windows 10 Pro (GGWA), Windows 10 Enterprise и Windows 10 Enterprise CSP | Антивирус из строки ноутбука. Кибер Бэкап Старт или Стандартная | Серверные редакции Windows, лицензии CAL, Remote Desktop Services и Rights Management. Чужой софт «для Windows»: DevExpress, Fortran, Dental4Windows, Paragon, Double-Take и остальные |
| Серверная Windows | 5 карточек самой серверной ОС: Windows Server Essentials 2019 и 2022, Datacenter 2022, Standard, MultiPoint Server Premium. Альт Сервер и Red Hat из таблиц выше получают тот же набор | Кибер Бэкап Расширенная, Хайстекс Акура, Arcserve. Альт Виртуализация или VMware vSphere. Dynamic Directory или Azure Active Directory | 9 карточек доступа: Windows Server CAL (2019, 2022, Open Value и без года), Remote Desktop Services CAL, в том числе карточки класса «удалённый доступ», Rights Management Services CAL и External Connector. К лицензии доступа серверный набор не вешается |
| VMware vSphere | 10 карточек со словом vSphere: сама vSphere 7 и комплекты Essentials, Essentials Plus, Standard, Enterprise Plus, Acceleration, Remote Office. Три карточки vCenter Server 7 для vSphere 7 получают тот же набор | Копии виртуальных машин: Кибер Бэкап, Altaro VM Backup, Хайстекс Акура. Kaspersky Security для виртуальных и облачных сред | Workstation 16 Player и Pro, Fusion 12, Horizon, vRealize, vSAN, ThinApp, Mirage, Workspace ONE, HCI Kit, Site Recovery Manager. Vinchin и VirtualMetric уже сами про копии и наблюдение. Workstation и Fusion ближе к рабочему месту: для них остаётся антивирус, серверный набор не ставится |
| Microsoft 365 | 6 карточек самого продукта: Family, Personal, Business Basic, бизнес стандарт, корпоративный (Enterprise), F3 | Те же антивирусы, что у офиса. Дополнительно Kaspersky Security для Microsoft Office 365 | F-Secure Elements for Microsoft 365, Kaspersky Security для Microsoft Office 365 и Office 365 Advanced Security Management: это уже защита, а не пакет. Коробочный Office, Word, Р7-Офис и МойОфис оставляют антивирус и не получают отдельную карточку Kaspersky для Office 365 |
| AnyDesk | Одна карточка `group:306539`, класс «удалённый доступ», 11 офферов-лицензий | Dr.Web, Kaspersky Endpoint, PRO32, MaxPatrol. Связь одна на карточку, лицензии внутри неё отдельно не связываются | Клиентские лицензии Windows Remote Desktop Services. TeamViewer, Radmin, RMS, АССИСТЕНТ и «10-Страйк» уже стоят в той же роли и получают ту же защиту устройства |

## Чего в этом списке нет

Связи не взяты из совместных покупок. Четыре ребра текущего поискового справочника (сервер к ОС, виртуализации и ИБП, резервное копирование к NAS) здесь не заменяются: ИБП остаётся оборудованием, а не софтом. Этот файл можно сжать до ролей, если перечень устроит.
