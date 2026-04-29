# Simuladores necesarios para cubrir la matriz de benchmarks de infer-network

## Qué implica “cubrirlo todo” con tu modelo

Tu especificación no pide “un simulador”, sino poder **generar familias de datasets** (bulk vs scRNA; muestras/células/tiempo/perturbación) y, además, producir **artefactos auxiliares** (*tf_list*, *groups*, *lineage_tree*, *prior_grn_by_group*, y *gold standards* globales y por grupo). La parte que más condiciona la elección de simuladores es la que tú mismo subrayas: para cubrir de verdad el modo completo tipo **scMTNI**, necesitas datasets con **grupos + un árbol entre grupos + una GRN distinta por grupo**, y además una **prior por grupo** (idealmente alineada a ese árbol). Esto no es un “extra”; es el núcleo del caso lineage-aware. citeturn11view0

En la práctica, eso significa que tu sistema de generación tiene que resolver dos problemas distintos:

- **Simular expresión** (bulk o single-cell; instantánea o dinámica; con o sin perturbaciones).
- **Producir o derivar redes verdaderas coherentes** (una global y/o una por grupo; a veces con “evolución” a lo largo de un linaje), y opcionalmente una **prior** (por ejemplo basada en accesibilidad/motif o, si no, una prior sintética degradada desde la verdad).

Los simuladores “clásicos” de benchmarks de GRN cubren muy bien bulk (sobre todo GNW/GeneNetWeaver). Para el bloque lineage-aware con prior, los simuladores single-cell generalistas suelen quedarse cortos si no añades un *pipeline* específico (como el que se describe explícitamente en el propio trabajo de scMTNI). citeturn9view0turn11view0

## Bulk RNA-seq: steady-state, time series y perturbacional

### GeneNetWeaver como columna vertebral de bulk

**GeneNetWeaver (GNW)** es, en la literatura de benchmarks, el caballo de batalla para datasets **bulk steady-state** y **bulk time series**, con experimentos tipo *wild-type*, *knockout*, *knockdown* y **perturbaciones multifactoriales**. El propio artículo describe que el simulador produce **datos steady-state y time-series** para esos tipos de experimentos. citeturn9view0

A nivel “producto”, el proyecto también lista como funcionalidad la simulación de **knockout/knockdown**, perturbaciones multifactoriales y **time series**. citeturn16view0

**Conclusión para tu matriz**  
- Familia 1 (bulk steady-state): GNW la cubre directamente. citeturn9view0  
- Familia 2 (bulk time series): GNW la cubre directamente. citeturn9view0  
- Familia 3 (bulk perturbacional / interventional): GNW cubre el caso “expresión bajo perturbaciones” (KO/KD/multifactorial). citeturn9view0turn16view0  

### GeneSPIDER / GeneSPIDER2 para control fino y diseño de perturbación

**GeneSPIDER (v2)** (ecosistema GeneSPIDER/GeneSPIDER2) está pensado como toolbox de benchmarking con control independiente de propiedades de red/datos (topología, estabilidad, SNR) y, crucialmente para tu familia 3, incluye **procedimientos para el diseño de experimentos de perturbación** (lo que en muchos métodos se materializa como una matriz de diseño/perturbación o metadatos equivalentes). citeturn13view1

**GeneSPIDER2** amplía capacidades y enfatiza dos puntos que te interesan por roadmap:
- Generación de **GRNs grandes** con propiedades topológicas realistas (grado tipo scale-free, modularidad). citeturn13view0  
- Simulación de **datos single-cell perturbacionales** (knockdown) con ruido específico de scRNA-seq; el artículo lo presenta como rasgo diferencial (“único”) para perturbed single-cell basado en GRN. citeturn13view0  

Para tu cobertura estricta actual, GeneSPIDER es especialmente útil si quieres que la familia 3 sea “de verdad interventional”, es decir, con un artefacto explícito de **diseño de perturbación** y no solo columnas etiquetadas como “perturbaciones”.

### Por qué tu schema debería contemplar (o al menos permitir) diseño de perturbación

Hay evidencia empírica de que, en benchmarks perturbacionales, **los métodos que usan el diseño de perturbación (matriz P)** pueden superar de forma consistente a los que no lo usan; el estudio lo formula de forma explícita (“knowledge of the perturbation design…”) y reporta que los métodos con diseño **superan significativamente** a los que no, usando datasets sintéticos generados con **GNW** y **GeneSPIDER**. citeturn17view0

Esto encaja con tu observación: si en el futuro integráis métodos que necesitan conocer **qué se perturbó por columna** (y cómo), necesitarás modelarlo como un extra del dataset (*perturbation design*), aunque hoy tu `column_kind=perturbations` no lo exprese formalmente. citeturn17view0

## scRNA-seq snapshot global y scRNA grouped

### SERGIO para scRNA steady-state y trayectorias (sin obligarte a multi-omics)

**SERGIO** está diseñado para simular expresión single-cell **guiada por una GRN** y declara explícitamente:
- simulación estocástica de expresión **en steady-state** o **en células en diferenciación**,
- posibilidad de simular **múltiples tipos celulares**,
- y uso en benchmarking, incluyendo **knockouts in silico**. citeturn10view0

**Cómo encaja en tu matriz**
- Familia 4 (scRNA snapshot global): SERGIO la cubre directamente (puedes simular una población sin *groups* o ignorar etiquetas de tipo). citeturn10view0  
- Familia 5 (scRNA grouped/clustered): SERGIO la cubre de forma natural porque permite simular “cualquier número de cell types”; eso te da `groups` de forma inmediata. citeturn10view0  

Limitación relevante para tus Bloques B/C: SERGIO te resuelve *groups*, pero **no te garantiza por sí mismo** “una GRN verdadera distinta por grupo” + “alineación a un árbol” como primer ciudadano del simulador (se puede construir alrededor, pero ahí ya estás montando pipeline).

### dyngen para que el linaje y el “ground truth por estado” no sea un apaño

**dyngen** se presenta como simulador multimodal de procesos dinámicos y, de forma importante para tu diseño:
- puede generar trayectorias no lineales (incluyendo topologías como **ramificadas**) y proporciona *ground truth* para varias tareas, incluyendo inferencia de red regulatoria a nivel de célula/estado. citeturn4view1turn14search5  
- el propio artículo explica que extraer una **red dinámica de verdad (“ground-truth dynamic network”)** es “straightforward” en su marco, porque pueden cuantificar el efecto de ausencia de un regulador sobre el target. citeturn4view1  
- la documentación muestra que el flujo incluye generación de una red de **TFs** (`generate_tf_network`), simulación del **gold standard**, y simulación de células; es decir, no solo expresión, también “artefactos” de red que puedes convertir en `tf_list` y gold standard. citeturn14search8turn14search1turn14search19  

**Cómo encaja en tu matriz**
- Familia 4–5: puede usarse para snapshot y grupos (definiendo grupos por *milestones*/*states* del backbone o por módulos). citeturn14search5turn14search8  
- Bloque B (grupos y red verdadera por grupo): dyngen es de los pocos donde es razonable derivar una **GRN verdadera por grupo** agregando/redondeando la red “activa” por estado/célula (porque el propio marco se plantea para ground truth de redes específicas por célula). citeturn4view1  
- Bloque C: también es viable, pero para `prior_grn_by_group` tendrás que diseñar un generador de priors (p. ej., degradando la verdad por grupo o usando un simulador multi-omics aparte), ya que dyngen no “te regala” una prior tipo scATAC. citeturn4view1turn11view0  

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["single-cell differentiation branching trajectory UMAP","gene regulatory network directed graph illustration","RNA velocity phase portrait single cell"],"num_per_query":1}

### scMultiSim si quieres priors realistas por grupo y/o multi-omics alineado a un árbol

**scMultiSim** está pensado como simulador “completo” de single-cell multi-omics. A nivel funcional, el README indica:
- entrada: **cell differential tree** + **GRN**,
- salida: gene expression, chromatin accessibility, RNA velocity, etc.,
- y además permite que la GRN sea **time-varying** (estructura que cambia temporalmente). citeturn3view0  

Esto es especialmente valioso para tu Bloque C, porque scMTNI usa priors por tipo celular derivadas de accesibilidad (scATAC) y un árbol de linaje como parte del input; scMultiSim, al simular accesibilidad, permite construir priors “del mismo mundo” que la expresión, en vez de priors sintéticas sin acoplamiento biológico.

## Linaje + prior por grupo: qué necesitas para cubrir scMTNI “de verdad”

Tu requisito más exigente (familias 6–7) coincide casi 1:1 con los inputs de scMTNI:

- scMTNI **toma como input**: *cell lineage tree* + scRNA por tipo + **prior networks por tipo** (derivadas de scATAC; y menciona que si no hay scATAC pueden usarse priors de otro tipo). citeturn11view0  
- **output**: un conjunto de GRNs específicas por cell type sobre el árbol. citeturn11view0  

Además, el propio paper describe un *pipeline* de simulación para benchmarking que es (casi) exactamente tu Bloque C:
- primero simulan GRNs para tipos celulares a través de un linaje usando un proceso probabilístico de **evolución de estructura de red**,
- luego generan expresión scRNA para cada tipo celular aplicando **BoolODE** sobre la red de ese tipo,
- y añaden alta esparsidad (80% ceros) para emular scRNA. citeturn11view0  

### BoolODE como pieza de “expresión + artefactos” en el pipeline lineage-aware

Si te importa reproducir el espíritu de scMTNI (y tener outputs directamente útiles para tus `extras`):
- BoolODE genera `refNetwork` (red de referencia), `PseudoTime` (pseudotiempo), `ExpressionData` y `ClusterIds`, y además su formato de expresión puede codificar **timepoints por experimento**, lo que ayuda para datasets temporales/pseudotemporales. citeturn12view0  
- incluye postprocesado para **dropouts**, lo que te permite acercarte a la esparsidad típica de scRNA. citeturn12view0  

Para tu familia 6 (lineage-aware grouped), este pipeline te da:
- `groups`: cell types / cluster IDs,  
- `lineage_tree`: el árbol que tú defines (y que ya usaste para evolucionar redes),  
- `gold standard por grupo`: la GRN simulada para cada tipo,  
- y puedes crear `prior_grn_by_group` degradando/perturbando la verdad por grupo o generando priors por heurística.

### Dos estrategias robustas para tu Bloque C

**Estrategia alineada con scMTNI (recomendada si el objetivo es “cobertura fiel”)**  
- Generador de GRNs por grupo a lo largo del árbol: puedes implementar el mismo concepto de “network structure evolution” (como en el paper) o reciclar un generador de variaciones controladas sobre una GRN base. citeturn11view0  
- Expresión por grupo: BoolODE (una ejecución por grupo/red). citeturn11view0turn12view0  
- `prior_grn_by_group`: si no simulas scATAC, genera priors degradadas desde la GRN verdadera por grupo; scMTNI explícitamente contempla priors alternativas si scATAC no está disponible. citeturn11view0  

**Estrategia multi-omics coherente (recomendada si quieres priors “tipo scATAC” sin inventarlas)**  
- Usa scMultiSim: ya trabaja con **cell differential tree** + GRN y simula accesibilidad; y permite GRN **time-varying**, lo que encaja con “GRN distinta por grupo/estado” si defines el calendario de cambios. citeturn3view0  
- Deriva `prior_grn_by_group` desde accesibilidad simulada (mismo principio que scMTNI usa en datos reales: prior por tipo a partir de accesibilidad/motifs). citeturn11view0turn3view0  

## Recomendación directa: qué simuladores necesitas, mínimo, para tu matriz

### Conjunto mínimo para cubrir A+B+C con bajo riesgo de huecos

**Para bulk (familias 1–3)**  
- **GeneNetWeaver** como baseline universal (steady-state, time series, KO/KD/multifactorial). citeturn9view0turn16view0  
- **GeneSPIDER(v2)/GeneSPIDER2** si quieres que el caso perturbacional sea “serio” (con control fino de propiedades y con posibilidad de representar explícitamente diseño de perturbación), y si te interesa además abrir camino a single-cell perturbacional tipo Perturb-seq en el futuro. citeturn13view1turn13view0turn17view0  

**Para scRNA snapshot global y grouped (familias 4–5)**  
- Opción simple: **SERGIO** (steady-state, múltiples cell types, diferenciación; KO in silico). citeturn10view0  
- Opción unificadora (si ya piensas en Bloque C): **dyngen** (trayectorias, ground truth y TF-network explícitos). citeturn4view1turn14search8turn14search5  

**Para scRNA lineage-aware + prior-by-group (familias 6–7)**  
Aquí necesitas algo que produzca (o te permita derivar) **red verdadera por grupo alineada al árbol** y una **prior por grupo**:
- Ruta “canon scMTNI”: **BoolODE + generador de evolución de red** (como hace el paper de scMTNI). citeturn11view0turn12view0  
- Ruta “multi-omics coherente”: **scMultiSim** (árbol + GRN, accesibilidad para priors, GRN time-varying). citeturn3view0turn11view0  

### Una lectura en una frase

- Si quieres **mínimo de piezas** y “todo” cubierto sin inventarte demasiado: **GNW (bulk) + pipeline tipo scMTNI (evolución de GRN + BoolODE) para el Bloque C**, y opcionalmente SERGIO/dyngen para enriquecer el Bloque A/B. citeturn9view0turn11view0turn12view0  
- Si quieres **priors realistas por grupo (tipo scATAC)**: añade **scMultiSim**. citeturn3view0turn11view0  
- Si quieres preparar el terreno para **single-cell perturbacional** (aunque no esté en tu matriz actual): **GeneSPIDER2** es el candidato directo por su foco en perturbación single-cell. citeturn13view0  

## Artefactos auxiliares: qué genera cada uno y qué tendrás que derivar

### Artefactos que salen casi directos

- `tf_list`:  
  - dyngen lo trata como objeto de primera clase (el pipeline arranca generando la red de TFs). citeturn14search8turn14search1  
  - scMTNI y otros métodos baseline requieren explícitamente lista de reguladores/targets; el paper lo menciona al describir la aplicación de algoritmos en los datasets simulados. citeturn11view0  

- `groups`:  
  - SERGIO: “cell types” simulados. citeturn10view0  
  - BoolODE: `ClusterIds.csv` como salida. citeturn12view0  

- `lineage_tree`:  
  - scMTNI: es input. Tienes que conservar el árbol que uses para simular (y para evaluar). citeturn11view0  
  - scMultiSim: el “cell differential tree” es parte del input; también lo puedes exportar tal cual. citeturn3view0  

### Artefactos que normalmente tendrás que construir tú

- `prior_grn_by_group`:  
  - scMTNI asume priors por cell type (idealmente desde scATAC). Si no simulas accesibilidad, tendrás que generar una prior sintética (por ejemplo: submuestreo de edges verdaderas + falsos positivos controlados + conservación parcial a lo largo del árbol). citeturn11view0  
  - scMultiSim es atractivo porque simula accesibilidad; eso permite derivar priors “tipo motivo/accesibilidad” con coherencia interna. citeturn3view0turn11view0  

- Gold standard por grupo (tu punto crítico):  
  - Si sigues el pipeline scMTNI, cada cell type tiene su GRN generada; eso es literalmente tu “verdad por grupo”. citeturn11view0  
  - Con dyngen, la vía robusta es derivar una red por grupo/estado desde el ground truth dinámico/celular (y documentar la regla de agregación). citeturn4view1  

### Nota final sobre perturbaciones y tiempos en el schema

Aunque hoy tu modelo distingue `column_kind=timepoints` o `column_kind=perturbations`, hay un salto práctico entre “marcar columnas” y proporcionar metadatos que algunos métodos necesitan (tiempos reales, dosis, o matriz explícita de diseño de perturbación). La evidencia de benchmarks sugiere que el **diseño de perturbación** puede ser determinante para lograr inferencias causales fiables. citeturn17view0