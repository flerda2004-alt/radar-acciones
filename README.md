# Radar de Acciones — Lynch + Graham

Página que se actualiza sola todos los días hábiles al cierre (19:30 hora Argentina) con la
señal **Comprar / Mantener / Vender** de cada acción de la watchlist, según:

- **Peter Lynch** (*One Up on Wall Street*, *Beating the Street*): categoría de la empresa, PEG,
  PEG ajustado por dividendos, PER normalizado para cíclicas, deuda, caja neta e inventarios.
- **Benjamin Graham** (*El inversor inteligente*, caps. 11, 14 y 15): los 7 criterios del inversor
  defensivo, los del inversor emprendedor, número de Graham y valor intrínseco
  `V = EPS × (8,5 + 2g) × 4,4 / Y`.
- **Señal combinada**: Comprar = +1, Mantener = 0, Vender = −1; se suman ambos marcos.

Cada acción tiene una ficha con cada criterio, su valor, el umbral y por qué cumple o no.

## Cómo funciona

```
GitHub Actions (lun-vie 19:30 ARG)
  ├─ Yahoo Finance  → precios, balances, dividendos, estimaciones de analistas
  ├─ SEC EDGAR      → 10+ años de EPS y ganancia neta (10-K / 20-F), ajustados por splits
  ├─ FRED           → tasa de bonos corporativos AAA (la "Y" de Graham)
  ├─ reglas Lynch + Graham
  └─ docs/index.html + data.json + historial.csv  →  GitHub Pages (URL pública)
                                                    └─ aviso por Telegram si cambia una señal
```

No hay que cargar datos a mano. Lo único manual es la lista de tickers (`watchlist.yaml`),
y solo cuando agregás o sacás una acción.

## Cómo usarlo

- **Ver el radar**: abrí https://flerda2004-alt.github.io/radar-acciones/ desde la compu o el celular.
  En el iPhone: Safari → botón Compartir → "Agregar a pantalla de inicio" y queda como una app.
- **Cuándo se actualiza**: solo, de lunes a viernes a las 19:30 (hora Argentina), con los precios de cierre.
  Si GitHub demora, puede tardar unos minutos más.
- **Leer una acción**: tocá la fila y se abre la ficha con cada criterio de Lynch y Graham, el valor
  y el umbral. Arriba podés ver la señal combinada o la de cada autor, filtrar y ordenar.
- **Compartir una acción**: abrí su ficha y copiá el link (termina en `#TICKER`, ej. `#MELI`).
- **Actualizar ya**: github.com/flerda2004-alt/radar-acciones → Actions → Actualizar radar → Run workflow.

## Dónde está

- Página: https://flerda2004-alt.github.io/radar-acciones/
- Código y configuración: https://github.com/flerda2004-alt/radar-acciones
- Copia local: ~/Documents/radar-acciones

## Puesta en marcha (ya hecha; queda como referencia si hay que rearmarlo)

1. **Crear el repositorio** en github.com → *New repository* → nombre `radar-acciones`, **Public**
   (GitHub Pages gratis requiere repo público; la página lleva `noindex` para no aparecer en Google).
2. **Subir el proyecto** desde la Terminal de la Mac, parado en esta carpeta:
   ```bash
   cd ~/Documents/radar-acciones
   git init -b main
   git add .
   git commit -m "Radar de acciones"
   git remote add origin https://github.com/TU_USUARIO/radar-acciones.git
   git push -u origin main
   ```
3. **Identificarte ante la SEC**: *Settings → Secrets and variables → Actions → New repository secret*
   - Nombre: `SEC_USER_AGENT` · Valor: `Renta Global SRL tu-mail@dominio.com`
4. **Activar la página**: *Settings → Pages → Build and deployment → Source: Deploy from a branch →
   Branch: `main` / carpeta `/docs` → Save*. La URL queda como `https://TU_USUARIO.github.io/radar-acciones/`.
5. **Primera corrida**: pestaña *Actions → Actualizar radar → Run workflow*. Tarda 3-6 minutos.
   Después corre sola de lunes a viernes.
6. Pegá la URL de la página en `config.yaml → sitio.url_publica` (para el link del aviso de Telegram).

## Agregar o sacar acciones

Desde el celular (app de GitHub o github.com): abrí `watchlist.yaml` → lápiz → agregá una línea
`  - TICKER` → *Commit changes*. Entra en la próxima corrida (o tocá *Run workflow*).

Formato Yahoo: `AAPL`, `BRK-B`, `GGAL.BA`, `YPFD.BA`. Si no estás de acuerdo con la categoría de
Lynch que asigna el sistema, se puede fijar:

```yaml
  - ticker: NVDA
    categoria_lynch: rapido   # rapido | estable | lento | ciclica | recuperacion
```

## Aviso por Telegram (opcional)

1. En Telegram, hablale a **@BotFather** → `/newbot` → copiá el token.
2. Mandale cualquier mensaje a tu bot y abrí `https://api.telegram.org/bot<TOKEN>/getUpdates`:
   el número en `"chat":{"id": ...}` es tu chat id (para un canal de clientes, agregá el bot al canal).
3. Cargá los secrets `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en GitHub (igual que en el paso 3).

Cada cierre llega un resumen con el conteo de señales y los cambios respecto del día anterior.

## Cambiar criterios

Todos los umbrales están en `config.yaml` con su fuente (PEG de compra/venta, PER máximo de Graham,
tope de crecimiento, años de dividendos, industrias cíclicas, etc.).

## Correrlo en la Mac (opcional)

```bash
cd ~/Documents/radar-acciones
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m radar.main --solo AAPL KO MELI     # prueba rápida
python -m radar.main                         # toda la watchlist
open docs/index.html
```

Tests sin conexión: `pip install pytest && python -m pytest -q`

## Decisiones de diseño que conviene conocer

- **ADRs y monedas**: los ratios de precio se calculan como capitalización total (USD) sobre
  magnitudes del balance convertidas a USD. Evita los errores típicos de Yahoo con ADRs
  (TSM = 5 acciones, BABA = 8) y balances en TWD, CNY o EUR.
- **Balances en pesos** (BYMA.BA, YPF): el crecimiento se mide en USD con el tipo de cambio de cada
  cierre; si no, el "crecimiento" es inflación. La página lo marca como aproximado.
- **Splits**: los EPS de la SEC se dividen por los splits posteriores a la fecha de presentación
  de cada balance (ej. NVDA 4:1 en 2021 y 10:1 en 2024).
- **Crecimiento**: mediana entre histórico (~5 años), estimación de analistas a 1-2 años y largo plazo.
  Tope de 50% para Lynch y de 15% dentro de la fórmula de Graham.
- **Bancos y aseguradoras**: no se les aplica liquidez corriente ni deuda/patrimonio; Lynch se
  evalúa con patrimonio/activos.
- **ETFs** (SPY, QQQ, DIA): se muestran como referencia, sin señal. Lynch y Graham evalúan empresas.
- **Turnarounds**: el sistema nunca los marca como compra; Lynch exige análisis caso por caso.
- Si fallan más de la mitad de los tickers (ej. Yahoo caído), no publica y conserva la última versión
  buena. GitHub te manda un mail cuando una corrida falla.
- La historia de señales queda en `docs/historial.csv` (una fila por ticker y día).
