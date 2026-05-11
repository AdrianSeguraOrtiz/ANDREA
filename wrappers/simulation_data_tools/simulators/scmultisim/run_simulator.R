args <- commandArgs(trailingOnly = TRUE)

parse_args <- function(values) {
  out <- list()
  idx <- 1
  while (idx <= length(values)) {
    key <- values[[idx]]
    if (!startsWith(key, "--")) {
      stop(sprintf("Unexpected argument: %s", key), call. = FALSE)
    }
    if (idx == length(values)) {
      stop(sprintf("Missing value for argument: %s", key), call. = FALSE)
    }
    out[[substring(key, 3)]] <- values[[idx + 1]]
    idx <- idx + 2
  }
  out
}

`%||%` <- function(lhs, rhs) {
  if (is.null(lhs)) rhs else lhs
}

is_true <- function(value) {
  isTRUE(value)
}

Ops.andrea_region_distrib <- function(e1, e2) {
  if (identical(.Generic, ">")) {
    return(all(unclass(e1) > e2))
  }
  NextMethod()
}

as_region_distrib <- function(value) {
  numeric_value <- as.numeric(value)
  if (length(numeric_value) != 3 || any(!is.finite(numeric_value)) || any(numeric_value <= 0)) {
    stop("atac.region_distrib must contain exactly three positive finite probabilities.", call. = FALSE)
  }
  if (!isTRUE(all.equal(sum(numeric_value), 1, tolerance = 1e-8))) {
    stop("atac.region_distrib probabilities must sum to one.", call. = FALSE)
  }
  structure(numeric_value, class = "andrea_region_distrib")
}

is.na.andrea_involved_genes <- function(x) {
  FALSE
}

as_involved_genes <- function(value) {
  structure(as.character(value), class = "andrea_involved_genes")
}

parsed <- parse_args(args)
required_keys <- c("request", "output-dir")
missing_keys <- required_keys[!required_keys %in% names(parsed)]
if (length(missing_keys) > 0) {
  stop(
    sprintf("Missing required arguments: %s", paste(missing_keys, collapse = ", ")),
    call. = FALSE
  )
}

suppressPackageStartupMessages({
  library(ape)
  library(jsonlite)
  library(scMultiSim)
})

request_path <- normalizePath(parsed[["request"]], mustWork = TRUE)
output_dir <- normalizePath(parsed[["output-dir"]], mustWork = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "extras"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "truth"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "provenance", "raw"), recursive = TRUE, showWarnings = FALSE)

progress_path <- file.path(output_dir, "progress.json")
raw_dir <- file.path(output_dir, "provenance", "raw")

write_json_atomic <- function(path, value) {
  tmp <- sprintf("%s.tmp", path)
  jsonlite::write_json(value, path = tmp, auto_unbox = TRUE, pretty = TRUE, null = "null")
  if (file.exists(path)) {
    file.remove(path)
  }
  ok <- file.rename(tmp, path)
  if (!ok) {
    stop(sprintf("Failed to write %s atomically.", path), call. = FALSE)
  }
}

write_progress <- function(status, phase, message = NULL, details = list()) {
  payload <- c(
    list(
      schema_version = "1.0",
      status = status,
      phase = phase,
      updated_at = format(Sys.time(), tz = "UTC", usetz = TRUE)
    ),
    if (!is.null(message)) list(message = message) else list(),
    if (length(details) > 0) list(details = details) else list()
  )
  write_json_atomic(progress_path, payload)
}

parse_request <- function(path) {
  req <- jsonlite::read_json(path, simplifyVector = TRUE)
  required_fields <- c("simulator_id", "profile", "seed", "effective_extras", "params", "runtime_resources")
  missing_fields <- required_fields[!required_fields %in% names(req)]
  if (length(missing_fields) > 0) {
    stop(
      sprintf(
        "simulator-run-request.json is missing required fields: %s",
        paste(missing_fields, collapse = ", ")
      ),
      call. = FALSE
    )
  }
  req
}

normalise_runtime_resources <- function(req) {
  resources <- req$runtime_resources
  raw_threads <- resources$threads %||% 1L
  if (
    length(raw_threads) != 1L ||
      is.na(raw_threads) ||
      raw_threads != floor(raw_threads) ||
      raw_threads < 1L
  ) {
    stop("runtime_resources.threads must be an integer >= 1.", call. = FALSE)
  }
  threads <- as.integer(raw_threads)
  list(threads = threads)
}

as_nullable_numeric <- function(value) {
  if (is.null(value)) {
    return(NULL)
  }
  as.numeric(value)
}

normalise_params <- function(req) {
  params <- req$params
  resources <- normalise_runtime_resources(req)
  list(
    grn_source = as.character(params$grn_source %||% "builtin_100"),
    tree_preset = as.character(params$tree_preset %||% "auto"),
    population_mode = as.character(params$population_mode %||% "auto"),
    num_cells = as.integer(params$num_cells %||% 1000L),
    threads = resources$threads,
    speed_up = is_true(params$speed_up %||% FALSE),
    num_genes = if (is.null(params$num_genes)) NULL else as.integer(params$num_genes),
    unregulated_gene_ratio = as.numeric(params$unregulated_gene_ratio %||% 0.1),
    grn_effect = as.numeric(params$grn_effect %||% 1.0),
    num_cifs = as.integer(params$num_cifs %||% 50L),
    diff_cif_fraction = as.numeric(params$diff_cif_fraction %||% 0.9),
    cif_center = as.numeric(params$cif_center %||% 1.0),
    cif_sigma = as.numeric(params$cif_sigma %||% 0.1),
    use_impulse = is_true(params$use_impulse %||% FALSE),
    discrete_population = list(
      pop_size = as.integer(params$discrete_population$pop_size %||% integer()),
      min_pop_size = as.integer(params$discrete_population$min_pop_size %||% 70L),
      min_pop_index = as.integer(params$discrete_population$min_pop_index %||% 1L)
    ),
    giv = list(
      mean = as.numeric(params$giv$mean %||% 0.0),
      prob = as.numeric(params$giv$prob %||% 0.3),
      sd = as.numeric(params$giv$sd %||% 1.0)
    ),
    high_expression = list(
      range = as.integer(params$high_expression$range %||% 1L),
      proportion = as.numeric(params$high_expression$proportion %||% 0.0),
      mean = as.numeric(params$high_expression$mean %||% 5.0),
      sd = as.numeric(params$high_expression$sd %||% 1.0),
      max_var = as.numeric(params$high_expression$max_var %||% 500.0)
    ),
    atac = list(
      effect = as.numeric(params$atac$effect %||% 0.5),
      region_distrib = as.numeric(params$atac$region_distrib %||% c(0.1, 0.5, 0.4)),
      p_zero = as.numeric(params$atac$p_zero %||% 0.8),
      riv_mean = as.numeric(params$atac$riv_mean %||% 0.0),
      riv_prob = as.numeric(params$atac$riv_prob %||% 0.3),
      riv_sd = as.numeric(params$atac$riv_sd %||% 1.0)
    ),
    rna_model = list(
      vary = as.character(params$rna_model$vary %||% "s"),
      bimod = as.numeric(params$rna_model$bimod %||% 0.0),
      scale_s = params$rna_model$scale_s %||% 1.0,
      intrinsic_noise = as.numeric(params$rna_model$intrinsic_noise %||% 1.0)
    ),
    velocity = list(
      enabled = is_true(params$velocity$enabled %||% TRUE),
      beta = as.numeric(params$velocity$beta %||% 0.4),
      d = as.numeric(params$velocity$d %||% 1.0),
      num_cycles = as.integer(params$velocity$num_cycles %||% 3L),
      cycle_len = as.numeric(params$velocity$cycle_len %||% 1.0)
    ),
    dynamic_grn = list(
      enabled = is_true(params$dynamic_grn$enabled %||% TRUE),
      num_steps = as.integer(params$dynamic_grn$num_steps %||% 200L),
      cell_per_step = as.integer(params$dynamic_grn$cell_per_step %||% 1L),
      involved_genes = as.character(params$dynamic_grn$involved_genes %||% character()),
      num_changing_edges = as.numeric(params$dynamic_grn$num_changing_edges %||% 2.0),
      create_tf_edges = is_true(params$dynamic_grn$create_tf_edges %||% FALSE),
      weight_mean = as_nullable_numeric(params$dynamic_grn$weight_mean),
      weight_sd = as.numeric(params$dynamic_grn$weight_sd %||% 1.0)
    ),
    technical_noise = list(
      enabled = is_true(params$technical_noise$enabled %||% FALSE),
      protocol = as.character(params$technical_noise$protocol %||% "nonUMI"),
      alpha_mean = as.numeric(params$technical_noise$alpha_mean %||% 0.1),
      alpha_sd = as.numeric(params$technical_noise$alpha_sd %||% 0.02),
      depth_mean = as.numeric(params$technical_noise$depth_mean %||% 100000.0),
      depth_sd = as.numeric(params$technical_noise$depth_sd %||% 3000.0),
      n_pcr1 = as.integer(params$technical_noise$n_pcr1 %||% 16L),
      n_pcr2 = as.integer(params$technical_noise$n_pcr2 %||% 10L),
      atac_obs_prob = as.numeric(params$technical_noise$atac_obs_prob %||% 0.3),
      atac_sd_frac = as.numeric(params$technical_noise$atac_sd_frac %||% 0.5)
    ),
    batch_effect = list(
      enabled = is_true(params$batch_effect$enabled %||% FALSE),
      num_batches = as.integer(params$batch_effect$num_batches %||% 2L),
      effect = as.numeric(params$batch_effect$effect %||% 3.0)
    ),
    mod_cif_giv_preset = as.character(params$mod_cif_giv_preset %||% "none"),
    ext_cif_giv_preset = as.character(params$ext_cif_giv_preset %||% "none")
  )
}

mounted_input_path <- function(request, input_id) {
  value <- request$mounted_inputs[[input_id]]
  if (is.null(value)) {
    return(NULL)
  }
  normalizePath(as.character(value), mustWork = TRUE)
}

read_grn_input <- function(path) {
  grn <- read.delim(path, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("target", "regulator", "effect")
  missing <- setdiff(required, names(grn))
  if (length(missing) > 0) {
    stop(sprintf("grn_params is missing required columns: %s", paste(missing, collapse = ", ")), call. = FALSE)
  }
  grn <- grn[, required, drop = FALSE]
  grn$effect <- as.numeric(grn$effect)
  if (anyNA(grn$effect)) {
    stop("grn_params effect column must be numeric.", call. = FALSE)
  }
  grn
}

load_grn <- function(params, request) {
  if (identical(params$grn_source, "builtin_100")) {
    data(GRN_params_100, package = "scMultiSim", envir = environment())
    return(GRN_params_100)
  }
  if (identical(params$grn_source, "builtin_1139")) {
    data(GRN_params_1139, package = "scMultiSim", envir = environment())
    return(GRN_params_1139)
  }
  if (identical(params$grn_source, "input_tsv")) {
    path <- mounted_input_path(request, "grn_params")
    if (is.null(path)) {
      stop("grn_params mounted input is required when grn_source=input_tsv.", call. = FALSE)
    }
    return(read_grn_input(path))
  }
  stop(sprintf("Unsupported grn_source: %s", params$grn_source), call. = FALSE)
}

load_tree <- function(params, request) {
  preset <- params$tree_preset
  if (identical(preset, "auto")) {
    preset <- if (identical(request$profile, "scrna_global")) "phyla1" else "phyla5"
  }
  if (identical(preset, "phyla1")) {
    return(Phyla1())
  }
  if (identical(preset, "phyla3")) {
    return(Phyla3())
  }
  if (identical(preset, "phyla5")) {
    return(Phyla5())
  }
  if (identical(preset, "input_newick")) {
    path <- mounted_input_path(request, "tree_newick")
    if (is.null(path)) {
      stop("tree_newick mounted input is required when tree_preset=input_newick.", call. = FALSE)
    }
    return(ape::read.tree(path))
  }
  stop(sprintf("Unsupported tree_preset: %s", params$tree_preset), call. = FALSE)
}

resolve_population_mode <- function(params, request) {
  mode <- params$population_mode
  if (identical(mode, "auto")) {
    return("continuous")
  }
  mode
}

grn_row_name_order <- function(grn) {
  as.character(sort(unique(c(grn[[1]], grn[[2]]))))
}

build_dynamic_grn_option <- function(params, grn) {
  if (!isTRUE(params$dynamic_grn$enabled)) {
    return(NA)
  }
  n_changed_edges <- if (params$dynamic_grn$num_changing_edges < 1) {
    as.integer(nrow(grn) * params$dynamic_grn$num_changing_edges)
  } else {
    as.integer(params$dynamic_grn$num_changing_edges)
  }
  if (n_changed_edges < 2) {
    stop(
      "dynamic_grn.num_changing_edges must resolve to at least two changed edges; scMultiSim 1.8.0 fails internally when exactly one edge is sampled.",
      call. = FALSE
    )
  }
  opt <- list(
    num.steps = params$dynamic_grn$num_steps,
    cell.per.step = params$dynamic_grn$cell_per_step,
    num.changing.edges = params$dynamic_grn$num_changing_edges,
    create.tf.edges = params$dynamic_grn$create_tf_edges,
    weight.sd = params$dynamic_grn$weight_sd
  )
  if (length(params$dynamic_grn$involved_genes) > 0) {
    opt$involved.genes <- as_involved_genes(params$dynamic_grn$involved_genes)
  } else {
    # scMultiSim's implicit dynamic.GRN default sorts remapped numeric gene ids,
    # while geff row positions follow character-sorted original IDs. Passing the
    # same all-gene set in geff row order preserves the default semantic and
    # avoids index mismatches in the upstream restructure() stopifnot checks.
    opt$involved.genes <- as_involved_genes(grn_row_name_order(grn))
  }
  if (!is.null(params$dynamic_grn$weight_mean)) {
    opt$weight.mean <- params$dynamic_grn$weight_mean
  }
  opt
}

build_sim_options <- function(request, params) {
  tree <- load_tree(params, request)
  grn <- load_grn(params, request)
  population_mode <- resolve_population_mode(params, request)
  opts <- list(
    rand.seed = as.integer(request$seed),
    threads = params$threads,
    speed.up = params$speed_up,
    GRN = grn,
    grn.effect = params$grn_effect,
    unregulated.gene.ratio = params$unregulated_gene_ratio,
    giv.mean = params$giv$mean,
    giv.prob = params$giv$prob,
    giv.sd = params$giv$sd,
    hge.range = params$high_expression$range,
    hge.prop = params$high_expression$proportion,
    hge.mean = params$high_expression$mean,
    hge.sd = params$high_expression$sd,
    hge.max.var = params$high_expression$max_var,
    dynamic.GRN = build_dynamic_grn_option(params, grn),
    num.cells = params$num_cells,
    tree = tree,
    discrete.cif = identical(population_mode, "discrete"),
    discrete.min.pop.size = params$discrete_population$min_pop_size,
    discrete.min.pop.index = params$discrete_population$min_pop_index,
    num.cifs = params$num_cifs,
    diff.cif.fraction = params$diff_cif_fraction,
    cif.center = params$cif_center,
    cif.sigma = params$cif_sigma,
    use.impulse = params$use_impulse,
    atac.effect = params$atac$effect,
    region.distrib = as_region_distrib(params$atac$region_distrib),
    atac.p_zero = params$atac$p_zero,
    riv.mean = params$atac$riv_mean,
    riv.prob = params$atac$riv_prob,
    riv.sd = params$atac$riv_sd,
    vary = params$rna_model$vary,
    bimod = params$rna_model$bimod,
    scale.s = params$rna_model$scale_s,
    intrinsic.noise = params$rna_model$intrinsic_noise,
    do.velocity = params$velocity$enabled,
    beta = params$velocity$beta,
    d = params$velocity$d,
    num.cycles = params$velocity$num_cycles,
    cycle.len = params$velocity$cycle_len
  )
  if (!is.null(params$num_genes)) {
    opts$num.genes <- params$num_genes
  }
  if (length(params$discrete_population$pop_size) > 0) {
    opts$discrete.pop.size <- as.integer(params$discrete_population$pop_size)
  }
  list(options = opts, tree = tree, grn = grn, population_mode = population_mode)
}

write_tsv <- function(value, path) {
  write.table(
    as.data.frame(value, stringsAsFactors = FALSE),
    file = path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
}

matrix_row_ids <- function(mat) {
  mat <- as.matrix(mat)
  row_ids <- rownames(mat)
  if (is.null(row_ids) || length(row_ids) != nrow(mat)) {
    return(as.character(seq_len(nrow(mat))))
  }
  row_ids <- as.character(row_ids)
  missing <- is.na(row_ids) | !nzchar(row_ids)
  if (all(missing)) {
    return(as.character(seq_len(nrow(mat))))
  }
  row_ids[missing] <- as.character(which(missing))
  row_ids
}

write_matrix_tsv <- function(mat, path, row_id = "gene") {
  mat <- as.matrix(mat)
  row_ids <- matrix_row_ids(mat)
  out <- data.frame(row_id_value = row_ids, mat, check.names = FALSE)
  names(out)[[1]] <- row_id
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

extract_counts_matrix <- function(results, params) {
  if (isTRUE(params$batch_effect$enabled)) {
    if (is.null(results$counts_with_batches)) {
      stop("batch_effect.enabled=true but counts_with_batches was not generated.", call. = FALSE)
    }
    return(as.matrix(results$counts_with_batches))
  }
  if (isTRUE(params$technical_noise$enabled)) {
    if (is.null(results$counts_obs)) {
      stop("technical_noise.enabled=true but counts_obs was not generated.", call. = FALSE)
    }
    if (is.list(results$counts_obs) && !is.null(results$counts_obs$counts)) {
      return(as.matrix(results$counts_obs$counts))
    }
    return(as.matrix(results$counts_obs))
  }
  as.matrix(results$counts)
}

gene_mapper <- function(grn_obj) {
  name_map <- tryCatch(grn_obj$name_map, error = function(e) NULL)
  if (is.null(name_map) || length(name_map) == 0) {
    return(function(ids) as.character(ids))
  }
  public_by_internal <- names(name_map)
  names(public_by_internal) <- as.character(unname(name_map))
  function(ids) {
    vapply(as.character(ids), function(id) {
      if (id %in% names(name_map)) {
        return(id)
      }
      if (id %in% names(public_by_internal)) {
        return(public_by_internal[[id]])
      }
      stripped <- sub("^gene", "", id)
      if (stripped %in% names(public_by_internal)) {
        return(public_by_internal[[stripped]])
      }
      id
    }, character(1))
  }
}

matrix_edges <- function(mat, grn_obj, context = "global", evidence = "simulated_truth") {
  mat <- as.matrix(mat)
  if (is.null(rownames(mat))) {
    rownames(mat) <- as.character(seq_len(nrow(mat)))
  }
  if (is.null(colnames(mat))) {
    colnames(mat) <- as.character(seq_len(ncol(mat)))
  }
  idx <- which(mat != 0 & !is.na(mat), arr.ind = TRUE)
  mapper <- gene_mapper(grn_obj)
  if (nrow(idx) == 0) {
    return(data.frame(
      source = character(),
      target = character(),
      score = numeric(),
      sign = character(),
      evidence = character(),
      context = character(),
      stringsAsFactors = FALSE
    ))
  }
  effects <- mat[idx]
  data.frame(
    source = mapper(colnames(mat)[idx[, "col"]]),
    target = mapper(rownames(mat)[idx[, "row"]]),
    score = abs(as.numeric(effects)),
    sign = ifelse(effects > 0, "+", ifelse(effects < 0, "-", "?")),
    evidence = evidence,
    context = context,
    stringsAsFactors = FALSE
  )
}

aggregate_matrices <- function(mats, grn_obj, context) {
  if (length(mats) == 0) {
    stop("Cannot aggregate zero GRN matrices.", call. = FALSE)
  }
  mats <- lapply(mats, as.matrix)
  sum_signed <- mats[[1]] * 0
  sum_abs <- mats[[1]] * 0
  for (mat in mats) {
    sum_signed <- sum_signed + mat
    sum_abs <- sum_abs + abs(mat)
  }
  mean_signed <- sum_signed / length(mats)
  mean_abs <- sum_abs / length(mats)
  if (is.null(rownames(mean_abs))) {
    rownames(mean_abs) <- rownames(mats[[1]])
  }
  if (is.null(colnames(mean_abs))) {
    colnames(mean_abs) <- colnames(mats[[1]])
  }
  idx <- which(mean_abs > 0 & !is.na(mean_abs), arr.ind = TRUE)
  mapper <- gene_mapper(grn_obj)
  if (nrow(idx) == 0) {
    return(data.frame(
      source = character(),
      target = character(),
      score = numeric(),
      sign = character(),
      evidence = character(),
      context = character(),
      stringsAsFactors = FALSE
    ))
  }
  signed <- mean_signed[idx]
  out <- data.frame(
    source = mapper(colnames(mean_abs)[idx[, "col"]]),
    target = mapper(rownames(mean_abs)[idx[, "row"]]),
    score = as.numeric(mean_abs[idx]),
    sign = ifelse(signed > 0, "+", ifelse(signed < 0, "-", "?")),
    evidence = "simulated_truth",
    context = context,
    stringsAsFactors = FALSE
  )
  out[order(out$source, out$target), , drop = FALSE]
}

write_global_truth <- function(results, params, path) {
  if (isTRUE(params$dynamic_grn$enabled) && !is.null(results$cell_specific_grn)) {
    truth_df <- aggregate_matrices(results$cell_specific_grn, results$.grn, "global")
  } else {
    truth_df <- matrix_edges(results$.grn$geff, results$.grn, "global")
  }
  if (nrow(truth_df) == 0) {
    stop("global_network derivation produced no nonzero edges.", call. = FALSE)
  }
  write.csv(truth_df, file = path, row.names = FALSE, quote = TRUE)
}

write_prior_grn <- function(results, path) {
  prior <- matrix_edges(results$.grn$geff, results$.grn, "global", "oracle_prior")
  prior_df <- data.frame(
    source = prior$source,
    target = prior$target,
    score = ifelse(prior$sign == "-", -prior$score, prior$score),
    stringsAsFactors = FALSE
  )
  prior_df <- prior_df[prior_df$score != 0, , drop = FALSE]
  if (nrow(prior_df) == 0) {
    stop("prior_grn derivation produced no nonzero edges.", call. = FALSE)
  }
  prior_df <- prior_df[order(prior_df$source, prior_df$target), , drop = FALSE]
  write.table(prior_df, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_tf_list <- function(results, path) {
  regulators <- results$.grn$regulators
  mapper <- gene_mapper(results$.grn)
  tf_ids <- unique(mapper(regulators))
  tf_ids <- tf_ids[nzchar(tf_ids)]
  if (length(tf_ids) == 0) {
    stop("tf_list derivation found no regulators.", call. = FALSE)
  }
  writeLines(tf_ids, con = path, sep = "\n", useBytes = TRUE)
}

write_enrichment_background <- function(expr, path) {
  genes <- matrix_row_ids(expr)
  genes <- unique(as.character(genes))
  genes <- genes[nzchar(genes)]
  if (length(genes) == 0) {
    stop("enrichment_background derivation found no genes.", call. = FALSE)
  }
  writeLines(genes, con = path, sep = "\n", useBytes = TRUE)
}

normalize_group_label <- function(value) {
  label <- gsub("[^A-Za-z0-9_.-]+", "_", as.character(value))
  label <- gsub("^_+|_+$", "", label)
  if (!nzchar(label)) {
    label <- "group"
  }
  if (grepl("^[0-9]", label)) {
    label <- paste0("pop_", label)
  }
  label
}

cell_ids_from_expression <- function(expr) {
  ids <- colnames(expr)
  if (is.null(ids)) {
    ids <- paste0("cell", seq_len(ncol(expr)))
  }
  as.character(ids)
}

derive_pseudotime_values <- function(results, expr, groups = NULL) {
  cell_ids <- cell_ids_from_expression(expr)
  values <- NULL
  if (!is.null(results$cell_time)) {
    values <- as.numeric(results$cell_time)
  } else if (!is.null(results$cell_meta) && "depth" %in% names(results$cell_meta)) {
    values <- as.numeric(results$cell_meta$depth)
  }
  if (is.null(values) || length(values) != length(cell_ids) || all(is.na(values))) {
    if (!is.null(groups)) {
      group_levels <- sort(unique(groups$cluster))
      group_order <- seq_along(group_levels) - 1L
      names(group_order) <- group_levels
      values <- as.numeric(group_order[groups$cluster])
    } else {
      values <- seq_along(cell_ids) - 1L
    }
  }
  values[is.na(values)] <- 0
  rng <- range(values, na.rm = TRUE)
  if (is.finite(rng[[1]]) && diff(rng) > 0) {
    values <- (values - rng[[1]]) / diff(rng)
  } else {
    values <- rep(0, length(values))
  }
  names(values) <- cell_ids
  values
}

write_pseudotime <- function(values, path) {
  out <- data.frame(
    cell = names(values),
    pseudotime = as.numeric(values),
    stringsAsFactors = FALSE
  )
  write_tsv(out, path)
}

derive_groups <- function(results, expr) {
  cell_ids <- cell_ids_from_expression(expr)
  if (is.null(results$cell_meta) || !"pop" %in% names(results$cell_meta)) {
    stop("groups derivation requires results$cell_meta$pop.", call. = FALSE)
  }
  pop <- as.character(results$cell_meta$pop)
  if (length(pop) != length(cell_ids)) {
    stop("cell_meta$pop length does not match expression cell count.", call. = FALSE)
  }
  data.frame(
    cell = cell_ids,
    cluster = vapply(pop, normalize_group_label, character(1)),
    stringsAsFactors = FALSE
  )
}

group_order_from_pseudotime <- function(groups_df, pseudotime_values) {
  df <- data.frame(
    cluster = groups_df$cluster,
    pseudotime = as.numeric(pseudotime_values[groups_df$cell]),
    stringsAsFactors = FALSE
  )
  agg <- aggregate(pseudotime ~ cluster, data = df, FUN = mean)
  agg <- agg[order(agg$pseudotime, agg$cluster), , drop = FALSE]
  order_values <- seq_len(nrow(agg)) - 1L
  names(order_values) <- agg$cluster
  order_values
}

write_groups <- function(groups_df, path) {
  write_tsv(groups_df, path)
}

write_cell_phenotypes <- function(groups_df, group_order, path) {
  out <- data.frame(
    cell = groups_df$cell,
    phenotype = groups_df$cluster,
    order = as.integer(group_order[groups_df$cluster]),
    stringsAsFactors = FALSE
  )
  out$order[is.na(out$order)] <- 0L
  write_tsv(out, path)
}

write_cluster_identities <- function(groups_df, group_order, path) {
  clusters <- sort(unique(groups_df$cluster))
  out <- data.frame(
    cluster = clusters,
    annotation = clusters,
    order = as.integer(group_order[clusters]),
    stringsAsFactors = FALSE
  )
  out$order[is.na(out$order)] <- seq_len(sum(is.na(out$order))) - 1L
  out <- out[order(out$order, out$cluster), , drop = FALSE]
  write_tsv(out, path)
}

slugify_group <- function(value) {
  slug <- gsub("[^A-Za-z0-9_.-]+", "_", as.character(value))
  slug <- gsub("^_+|_+$", "", slug)
  if (!nzchar(slug)) {
    slug <- "group"
  }
  slug
}

derive_group_networks <- function(results, groups_df, output_dir, raw_dir, export_public = TRUE) {
  if (is.null(results$cell_specific_grn)) {
    stop("group network derivation requires dynamic_grn.enabled=true and results$cell_specific_grn.", call. = FALSE)
  }
  cell_ids <- groups_df$cell
  mats <- results$cell_specific_grn
  if (length(mats) != length(cell_ids)) {
    stop("cell_specific_grn length does not match expression cell count.", call. = FALSE)
  }
  used_groups <- unique(groups_df$cluster)
  group_slugs <- make.unique(vapply(used_groups, slugify_group, character(1)), sep = "_")
  names(group_slugs) <- used_groups
  all_activity <- list()
  group_networks <- list()
  active_edges_by_group <- list()

  for (group_name in used_groups) {
    idx <- which(groups_df$cluster == group_name)
    truth_df <- aggregate_matrices(mats[idx], results$.grn, paste0("group:", group_name))
    active_edges_by_group[[group_name]] <- paste(truth_df$source, truth_df$target, sep = "->")
    all_activity[[group_name]] <- data.frame(
      cluster = group_name,
      regulator = truth_df$source,
      target = truth_df$target,
      mean_abs_strength = truth_df$score,
      mean_signed_strength = ifelse(truth_df$sign == "-", -truth_df$score, truth_df$score),
      cells_in_group = length(idx),
      active = nrow(truth_df) > 0,
      stringsAsFactors = FALSE
    )
    if (isTRUE(export_public)) {
      rel_path <- file.path("truth", "group_networks", paste0(group_slugs[[group_name]], ".csv"))
      dir.create(file.path(output_dir, "truth", "group_networks"), recursive = TRUE, showWarnings = FALSE)
      write.csv(truth_df, file = file.path(output_dir, rel_path), row.names = FALSE, quote = TRUE)
      group_networks[[length(group_networks) + 1L]] <- list(group = group_name, path = rel_path)
    }
  }

  activity <- do.call(rbind, all_activity)
  if (is.null(activity)) {
    activity <- data.frame(
      cluster = character(),
      regulator = character(),
      target = character(),
      mean_abs_strength = numeric(),
      mean_signed_strength = numeric(),
      cells_in_group = integer(),
      active = logical(),
      stringsAsFactors = FALSE
    )
  }
  activity$edge_id <- paste(activity$regulator, activity$target, sep = "->")
  write_tsv(activity, file.path(raw_dir, "group_edge_activity.tsv"))
  write_tsv(activity[activity$active, c("cluster", "regulator", "target", "edge_id"), drop = FALSE], file.path(raw_dir, "group_active_networks.tsv"))
  if (isTRUE(export_public)) {
    write_tsv(
      data.frame(
        group = vapply(group_networks, function(item) item$group, character(1)),
        path = vapply(group_networks, function(item) item$path, character(1)),
        stringsAsFactors = FALSE
      ),
      file.path(raw_dir, "group_networks_index.tsv")
    )
  }
  list(
    group_networks = group_networks,
    group_edge_activity = activity,
    active_edges_by_group = active_edges_by_group
  )
}

write_prior_grn_by_group <- function(group_edge_activity, path) {
  active <- group_edge_activity[group_edge_activity$active, , drop = FALSE]
  score <- active$mean_signed_strength
  fallback <- is.na(score) | abs(score) <= .Machine$double.eps
  score[fallback] <- active$mean_abs_strength[fallback]
  out <- data.frame(
    group = active$cluster,
    source = active$regulator,
    target = active$target,
    score = as.numeric(score),
    stringsAsFactors = FALSE
  )
  out <- out[!is.na(out$score) & out$score != 0, , drop = FALSE]
  if (nrow(out) == 0) {
    stop("prior_grn_by_group derivation produced no nonzero edges.", call. = FALSE)
  }
  out <- out[order(out$group, out$source, out$target), , drop = FALSE]
  write_tsv(out, path)
}

derive_lineage_edges <- function(groups_df, group_order) {
  groups <- names(sort(group_order))
  parsed <- strsplit(sub("^pop_", "", groups), "_", fixed = TRUE)
  starts <- vapply(parsed, function(x) if (length(x) >= 2) x[[1]] else NA_character_, character(1))
  ends <- vapply(parsed, function(x) if (length(x) >= 2) x[[2]] else NA_character_, character(1))
  edges <- data.frame(parent = character(), child = character(), stringsAsFactors = FALSE)
  for (i in seq_along(groups)) {
    parent_candidates <- which(!is.na(ends) & !is.na(starts[[i]]) & ends == starts[[i]])
    parent_candidates <- setdiff(parent_candidates, i)
    if (length(parent_candidates) > 0) {
      parent <- groups[parent_candidates[order(group_order[parent_candidates])][[1]]]
      edges <- rbind(edges, data.frame(parent = parent, child = groups[[i]], stringsAsFactors = FALSE))
    }
  }
  if (nrow(edges) == 0 && length(groups) > 1) {
    edges <- data.frame(
      parent = groups[-length(groups)],
      child = groups[-1],
      stringsAsFactors = FALSE
    )
  }
  edges
}

write_lineage_tree <- function(groups_df, group_order, active_edges_by_group, path, raw_dir) {
  lineage_edges <- derive_lineage_edges(groups_df, group_order)
  if (nrow(lineage_edges) == 0) {
    stop("lineage_tree derivation requires at least two exported groups.", call. = FALSE)
  }
  lineage_tree <- do.call(rbind, lapply(seq_len(nrow(lineage_edges)), function(idx) {
    parent <- lineage_edges$parent[[idx]]
    child <- lineage_edges$child[[idx]]
    parent_edges <- active_edges_by_group[[parent]] %||% character()
    child_edges <- active_edges_by_group[[child]] %||% character()
    gained <- setdiff(child_edges, parent_edges)
    lost <- setdiff(parent_edges, child_edges)
    data.frame(
      child = child,
      parent = parent,
      gain_rate = length(gained) / max(length(child_edges), 1L),
      loss_rate = length(lost) / max(length(parent_edges), 1L),
      stringsAsFactors = FALSE
    )
  }))
  write_tsv(lineage_tree, path)
  write_tsv(data.frame(group = names(group_order), order = as.integer(group_order), stringsAsFactors = FALSE), file.path(raw_dir, "lineage_states_used.tsv"))
  write_tsv(lineage_edges, file.path(raw_dir, "lineage_transitions_used.tsv"))
}

write_native_outputs <- function(results, native_output_ids, output_dir) {
  requested <- unique(as.character(native_output_ids))
  requested <- requested[nzchar(requested)]
  if (length(requested) == 0) {
    return(structure(list(), names = character()))
  }
  known <- c("true_counts", "observed_counts", "atac_counts", "cell_meta", "velocity", "cell_specific_grn")
  unknown <- setdiff(requested, known)
  if (length(unknown) > 0) {
    stop(sprintf("Unsupported native_output requested: %s", paste(unknown, collapse = ", ")), call. = FALSE)
  }
  require_native <- function(id, value, message) {
    if (id %in% requested && is.null(value)) {
      stop(message, call. = FALSE)
    }
  }
  require_native("true_counts", results$counts, "native_output true_counts is unavailable: results$counts is missing.")
  require_native("observed_counts", results$counts_obs, "native_output observed_counts requires technical_noise.enabled=true so add_expr_noise creates results$counts_obs.")
  require_native("atac_counts", results$atac_counts, "native_output atac_counts is unavailable: results$atac_counts is missing from scMultiSim output.")
  require_native("cell_meta", results$cell_meta, "native_output cell_meta is unavailable: results$cell_meta is missing.")
  require_native("velocity", results$velocity, "native_output velocity requires velocity.enabled=true so sim_true_counts emits results$velocity.")
  require_native("cell_specific_grn", results$cell_specific_grn, "native_output cell_specific_grn requires dynamic_grn.enabled=true so sim_true_counts emits results$cell_specific_grn.")
  dir.create(file.path(output_dir, "native"), recursive = TRUE, showWarnings = FALSE)
  manifest <- list()
  if ("true_counts" %in% requested) {
    write_matrix_tsv(results$counts, file.path(output_dir, "native", "true_counts.tsv"))
    manifest$true_counts <- "native/true_counts.tsv"
  }
  if ("observed_counts" %in% requested && !is.null(results$counts_obs)) {
    observed <- if (is.list(results$counts_obs) && !is.null(results$counts_obs$counts)) results$counts_obs$counts else results$counts_obs
    write_matrix_tsv(observed, file.path(output_dir, "native", "observed_counts.tsv"))
    manifest$observed_counts <- "native/observed_counts.tsv"
  }
  if ("atac_counts" %in% requested && !is.null(results$atac_counts)) {
    write_matrix_tsv(results$atac_counts, file.path(output_dir, "native", "atac_counts.tsv"), row_id = "region")
    manifest$atac_counts <- "native/atac_counts.tsv"
  }
  if ("cell_meta" %in% requested && !is.null(results$cell_meta)) {
    write_tsv(results$cell_meta, file.path(output_dir, "native", "cell_meta.tsv"))
    manifest$cell_meta <- "native/cell_meta.tsv"
  }
  if ("velocity" %in% requested && !is.null(results$velocity)) {
    write_matrix_tsv(results$velocity, file.path(output_dir, "native", "velocity.tsv"))
    manifest$velocity <- "native/velocity.tsv"
  }
  if ("cell_specific_grn" %in% requested && !is.null(results$cell_specific_grn)) {
    saveRDS(results$cell_specific_grn, file.path(output_dir, "native", "cell_specific_grn.rds"), compress = TRUE)
    manifest$cell_specific_grn <- "native/cell_specific_grn.rds"
  }
  manifest
}

write_manifest <- function(request, results, expr, output_dir, group_networks = list(), native_outputs = structure(list(), names = character())) {
  manifest <- list(
    schema_version = "1.0",
    simulator_id = request$simulator_id,
    profile = request$profile,
    seed = as.integer(request$seed),
    expression = list(
      path = "expression.tsv",
      genes = nrow(expr),
      columns = ncol(expr),
      column_kind = "cells",
      expression_profile = "scrna"
    ),
    extras = list(
      groups = if (file.exists(file.path(output_dir, "extras", "groups.tsv"))) "extras/groups.tsv" else NULL,
      cell_phenotypes = if (file.exists(file.path(output_dir, "extras", "cell_phenotypes.tsv"))) "extras/cell_phenotypes.tsv" else NULL,
      cluster_identities = if (file.exists(file.path(output_dir, "extras", "cluster_identities.tsv"))) "extras/cluster_identities.tsv" else NULL,
      enrichment_background = if (file.exists(file.path(output_dir, "extras", "enrichment_background.txt"))) "extras/enrichment_background.txt" else NULL,
      lineage_tree = if (file.exists(file.path(output_dir, "extras", "lineage_tree.tsv"))) "extras/lineage_tree.tsv" else NULL,
      pseudotime = if (file.exists(file.path(output_dir, "extras", "pseudotime.tsv"))) "extras/pseudotime.tsv" else NULL,
      prior_grn = if (file.exists(file.path(output_dir, "extras", "prior_grn.tsv"))) "extras/prior_grn.tsv" else NULL,
      tf_list = if (file.exists(file.path(output_dir, "extras", "tf_list.txt"))) "extras/tf_list.txt" else NULL,
      prior_grn_by_group = if (file.exists(file.path(output_dir, "extras", "prior_grn_by_group.tsv"))) "extras/prior_grn_by_group.tsv" else NULL
    ),
    native_outputs = if (length(native_outputs) > 0) native_outputs else structure(list(), names = character()),
    truth = list(
      global_network = "truth/global_network.csv",
      group_networks = group_networks
    ),
    provenance = list(
      raw_dir = "provenance/raw",
      notes = sprintf(
        "Bioconductor scMultiSim %s run through sim_true_counts() with ANDREA wrapper-normalized outputs.",
        as.character(utils::packageVersion("scMultiSim"))
      )
    )
  )
  write_json_atomic(file.path(output_dir, "simulator-output-manifest.json"), manifest)
}

request <- parse_request(request_path)
params <- normalise_params(request)
effective_extras <- unique(as.character(request$effective_extras))
native_outputs <- unique(as.character(request$native_outputs %||% character()))

write_json_atomic(file.path(raw_dir, "simulator-run-request.json"), request)
write_json_atomic(
  file.path(raw_dir, "wrapper_environment.json"),
  list(
    scMultiSim_version = as.character(utils::packageVersion("scMultiSim")),
    R_version = paste(R.version$major, R.version$minor, sep = "."),
    runtime_resources = request$runtime_resources
  )
)
writeLines(capture.output(sessionInfo()), con = file.path(raw_dir, "session_info.txt"))

write_progress("running", "validate_request", "Validating scMultiSim wrapper request.")

tryCatch(
  {
    if (params$batch_effect$enabled && !params$technical_noise$enabled) {
      stop("batch_effect.enabled=true requires technical_noise.enabled=true.", call. = FALSE)
    }
    needs_group_specific <- any(c("group_networks", "prior_grn_by_group", "lineage_tree") %in% effective_extras)
    if (needs_group_specific && !params$dynamic_grn$enabled) {
      stop("group_networks, prior_grn_by_group and lineage_tree require dynamic_grn.enabled=true.", call. = FALSE)
    }
    if (!identical(params$mod_cif_giv_preset, "none")) {
      stop("Only mod_cif_giv_preset='none' is supported.", call. = FALSE)
    }
    if (!identical(params$ext_cif_giv_preset, "none")) {
      stop("Only ext_cif_giv_preset='none' is supported.", call. = FALSE)
    }

    set.seed(as.integer(request$seed))
    write_progress("running", "prepare_options", "Preparing scMultiSim options.")
    built <- build_sim_options(request, params)
    write_json_atomic(
      file.path(raw_dir, "resolved_wrapper_params.json"),
      params
    )
    saveRDS(built$options, file.path(raw_dir, "scmultisim_options.rds"), compress = TRUE)

    write_progress("running", "run_simulator", "Running scMultiSim::sim_true_counts().")
    results <- scMultiSim::sim_true_counts(built$options)

    if (params$technical_noise$enabled) {
      write_progress("running", "technical_noise", "Running scMultiSim::add_expr_noise().")
      scMultiSim::add_expr_noise(
        results,
        randseed = as.integer(request$seed),
        protocol = params$technical_noise$protocol,
        alpha_mean = params$technical_noise$alpha_mean,
        alpha_sd = params$technical_noise$alpha_sd,
        depth_mean = params$technical_noise$depth_mean,
        depth_sd = params$technical_noise$depth_sd,
        nPCR1 = params$technical_noise$n_pcr1,
        nPCR2 = params$technical_noise$n_pcr2,
        atac.obs.prob = params$technical_noise$atac_obs_prob,
        atac.sd.frac = params$technical_noise$atac_sd_frac
      )
    }
    if (params$batch_effect$enabled) {
      write_progress("running", "batch_effect", "Running scMultiSim::divide_batches().")
      scMultiSim::divide_batches(
        results,
        nbatch = params$batch_effect$num_batches,
        effect = params$batch_effect$effect,
        randseed = as.integer(request$seed)
      )
    }

    write_progress("running", "package_outputs", "Writing normalized ANDREA outputs.")
    expr <- extract_counts_matrix(results, params)
    write_matrix_tsv(expr, file.path(output_dir, "expression.tsv"))
    saveRDS(results, file.path(raw_dir, "result.rds"), compress = TRUE)
    write_matrix_tsv(results$counts, file.path(raw_dir, "true_counts.tsv"))
    write_tsv(results$cell_meta, file.path(raw_dir, "cell_meta.tsv"))
    write_global_truth(results, params, file.path(output_dir, "truth", "global_network.csv"))
    exported_native_outputs <- write_native_outputs(results, native_outputs, output_dir)

    need_groups <- identical(request$profile, "scrna_grouped") || any(c("groups", "cell_phenotypes", "cluster_identities", "lineage_tree", "prior_grn_by_group", "group_networks") %in% effective_extras)
    groups_df <- NULL
    pseudotime_values <- NULL
    group_order <- NULL
    group_network_result <- NULL
    group_networks <- list()

    if (need_groups) {
      groups_df <- derive_groups(results, expr)
      write_groups(groups_df, file.path(output_dir, "extras", "groups.tsv"))
      pseudotime_values <- derive_pseudotime_values(results, expr, groups_df)
      group_order <- group_order_from_pseudotime(groups_df, pseudotime_values)
    }

    if ("enrichment_background" %in% effective_extras) {
      write_enrichment_background(expr, file.path(output_dir, "extras", "enrichment_background.txt"))
    }
    if ("pseudotime" %in% effective_extras) {
      if (is.null(pseudotime_values)) {
        pseudotime_values <- derive_pseudotime_values(results, expr)
      }
      write_pseudotime(pseudotime_values, file.path(output_dir, "extras", "pseudotime.tsv"))
    }
    if ("prior_grn" %in% effective_extras) {
      write_prior_grn(results, file.path(output_dir, "extras", "prior_grn.tsv"))
    }
    if ("tf_list" %in% effective_extras) {
      write_tf_list(results, file.path(output_dir, "extras", "tf_list.txt"))
    }

    if (!is.null(groups_df)) {
      if ("cell_phenotypes" %in% effective_extras) {
        write_cell_phenotypes(groups_df, group_order, file.path(output_dir, "extras", "cell_phenotypes.tsv"))
      }
      if ("cluster_identities" %in% effective_extras) {
        write_cluster_identities(groups_df, group_order, file.path(output_dir, "extras", "cluster_identities.tsv"))
      }
      if (any(c("group_networks", "prior_grn_by_group", "lineage_tree") %in% effective_extras)) {
        write_progress("running", "derive_group_truth", "Deriving group regulatory truth from cell_specific_grn.")
        group_network_result <- derive_group_networks(
          results,
          groups_df,
          output_dir,
          raw_dir,
          export_public = "group_networks" %in% effective_extras
        )
        group_networks <- group_network_result$group_networks
      }
      if ("prior_grn_by_group" %in% effective_extras) {
        write_prior_grn_by_group(
          group_network_result$group_edge_activity,
          file.path(output_dir, "extras", "prior_grn_by_group.tsv")
        )
      }
      if ("lineage_tree" %in% effective_extras) {
        write_lineage_tree(
          groups_df,
          group_order,
          group_network_result$active_edges_by_group,
          file.path(output_dir, "extras", "lineage_tree.tsv"),
          raw_dir
        )
      }
    }

    write_progress("running", "write_manifest", "Writing simulator-output-manifest.json.")
    write_manifest(
      request,
      results,
      expr,
      output_dir,
      group_networks = group_networks,
      native_outputs = exported_native_outputs
    )
    write_progress("done", "done", "scMultiSim wrapper completed successfully.")
  },
  error = function(exc) {
    write_progress("failed", "failed", conditionMessage(exc))
    stop(exc)
  }
)
