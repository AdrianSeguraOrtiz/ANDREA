#!/usr/bin/env Rscript

Sys.setenv(
  OMP_NUM_THREADS = "1",
  OPENBLAS_NUM_THREADS = "1",
  MKL_NUM_THREADS = "1",
  BLIS_NUM_THREADS = "1",
  VECLIB_MAXIMUM_THREADS = "1"
)

suppressPackageStartupMessages({
  suppressWarnings(library(jsonlite))
  suppressWarnings(library(ScReNI))
})

SUPPORTED_MODES <- c("column_native", "group_aggregated")

`%||%` <- function(x, y) {
  if (is.null(x)) y else x
}

parse_args <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  out <- list()
  i <- 1L
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      stop(sprintf("Unknown argument: %s", key), call. = FALSE)
    }
    if (i == length(args)) {
      stop(sprintf("Missing value for %s", key), call. = FALSE)
    }
    value <- args[[i + 1L]]
    if (startsWith(value, "--")) {
      stop(sprintf("Missing value for %s", key), call. = FALSE)
    }
    out[[substring(key, 3L)]] <- value
    i <- i + 2L
  }
  out
}

write_progress <- function(progress_path, status, percent, phase, message,
                           completed = NULL, total = NULL, error = NULL) {
  payload <- list(
    status = status,
    phase = phase,
    percent = max(0L, min(100L, as.integer(percent))),
    message = message,
    timestamp = as.numeric(Sys.time())
  )
  if (!is.null(completed)) payload$completed <- as.integer(completed)
  if (!is.null(total)) payload$total <- as.integer(total)
  if (!is.null(error)) payload$error <- as.character(error)

  tmp_path <- paste0(progress_path, ".tmp")
  writeLines(toJSON(payload, auto_unbox = TRUE, null = "null"), tmp_path, useBytes = TRUE)
  if (!file.rename(tmp_path, progress_path)) {
    stop("Failed to write progress.json atomically.", call. = FALSE)
  }
}

append_log <- function(log_path, message) {
  cat(
    paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), " ", message, "\n"),
    file = log_path,
    append = TRUE
  )
}

is_scalar_number <- function(x) is.numeric(x) && length(x) == 1L && !is.na(x)

as_int_checked <- function(name, value, min_value = NULL) {
  if (!is_scalar_number(value) || abs(value - round(value)) > 1e-9) {
    stop(sprintf("%s must be an integer.", name), call. = FALSE)
  }
  out <- as.integer(round(value))
  if (!is.null(min_value) && out < min_value) {
    stop(sprintf("%s must be >= %d.", name, as.integer(min_value)), call. = FALSE)
  }
  out
}

load_params <- function(params_path) {
  params <- fromJSON(params_path, simplifyVector = TRUE)
  if (!is.list(params)) {
    stop("params.json must be a JSON object.", call. = FALSE)
  }
  params
}

resolve_params <- function(raw_params) {
  expected <- c("nfeatures", "knn")
  missing <- setdiff(expected, names(raw_params))
  if (length(missing) > 0L) {
    stop(
      sprintf("Missing required params in params.json: %s", paste(missing, collapse = ", ")),
      call. = FALSE
    )
  }

  unknown <- setdiff(names(raw_params), expected)
  if (length(unknown) > 0L) {
    warning(sprintf("Unknown params ignored: %s", paste(sort(unknown), collapse = ", ")))
  }

  list(
    nfeatures = as_int_checked("nfeatures", raw_params$nfeatures, min_value = 1L),
    knn = as_int_checked("knn", raw_params$knn, min_value = 1L)
  )
}

load_execution_mode <- function(params_path) {
  execution_path <- file.path(dirname(params_path), "execution.json")
  if (!file.exists(execution_path)) {
    return("column_native")
  }
  execution <- fromJSON(execution_path, simplifyVector = TRUE)
  if (!is.list(execution)) {
    stop("execution.json must be a JSON object.", call. = FALSE)
  }
  mode <- execution$mode %||% "column_native"
  if (!is.character(mode) || length(mode) != 1L || is.na(mode)) {
    stop("execution.mode must be a string.", call. = FALSE)
  }
  if (!(mode %in% SUPPORTED_MODES)) {
    stop(
      "kScReNI supports only execution.mode=column_native or execution.mode=group_aggregated.",
      call. = FALSE
    )
  }
  mode
}

read_header <- function(path) {
  con <- file(path, open = "r")
  on.exit(close(con), add = TRUE)
  line <- readLines(con, n = 1L, warn = FALSE)
  if (length(line) == 0L || !nzchar(line)) {
    stop(sprintf("%s is empty.", basename(path)), call. = FALSE)
  }
  strsplit(line, "\t", fixed = TRUE)[[1L]]
}

read_expression_tsv <- function(expr_path) {
  header <- read_header(expr_path)
  if (length(header) < 3L) {
    stop("expression.tsv must have a gene column and at least 2 cells.", call. = FALSE)
  }

  cell_ids <- header[-1L]
  if (any(!nzchar(cell_ids))) {
    stop("expression.tsv contains an empty cell identifier.", call. = FALSE)
  }
  if (anyDuplicated(cell_ids)) {
    duplicated <- sort(unique(cell_ids[duplicated(cell_ids)]))
    stop(
      sprintf("expression.tsv contains duplicated cell identifiers: %s", paste(duplicated, collapse = ", ")),
      call. = FALSE
    )
  }

  df <- read.delim(
    expr_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(df) != length(header)) {
    stop("expression.tsv rows do not match the header width.", call. = FALSE)
  }

  gene_ids <- as.character(df[[1L]])
  if (any(!nzchar(gene_ids))) {
    stop("expression.tsv contains an empty gene identifier.", call. = FALSE)
  }
  if (anyDuplicated(gene_ids)) {
    duplicated <- sort(unique(gene_ids[duplicated(gene_ids)]))
    stop(
      sprintf("expression.tsv contains duplicated genes: %s", paste(duplicated, collapse = ", ")),
      call. = FALSE
    )
  }

  num_df <- data.frame(
    lapply(df[, -1L, drop = FALSE], function(x) suppressWarnings(as.numeric(x))),
    check.names = FALSE
  )
  mat <- as.matrix(num_df)
  if (anyNA(mat) || any(!is.finite(mat))) {
    stop("expression.tsv contains non-finite or non-numeric expression values.", call. = FALSE)
  }
  if (any(mat < 0)) {
    stop("kScReNI expects non-negative scRNA-seq counts.", call. = FALSE)
  }

  rownames(mat) <- gene_ids
  colnames(mat) <- cell_ids
  mat
}

validate_expression_for_params <- function(expr, params) {
  if (nrow(expr) < 2L) {
    stop("kScReNI requires at least 2 genes.", call. = FALSE)
  }
  if (ncol(expr) < 2L) {
    stop("kScReNI requires at least 2 cells.", call. = FALSE)
  }
  if (params$knn + 1L > ncol(expr)) {
    stop(
      sprintf(
        "knn + 1 must be <= number of cells (%d); got knn=%d.",
        ncol(expr),
        params$knn
      ),
      call. = FALSE
    )
  }
}

validate_groups <- function(extra_dir, cell_ids) {
  groups_path <- file.path(extra_dir, "groups.tsv")
  if (!file.exists(groups_path)) {
    stop("groups.tsv is required for kScReNI execution.mode=group_aggregated.", call. = FALSE)
  }
  header <- read_header(groups_path)
  if (length(header) < 2L || !("cluster" %in% header[-1L])) {
    stop("groups.tsv must contain a first expression-column id column and a cluster column.", call. = FALSE)
  }

  df <- read.delim(
    groups_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  group_cell_ids <- as.character(df[[1L]])
  if (any(!nzchar(group_cell_ids))) {
    stop("groups.tsv contains an empty expression-column identifier.", call. = FALSE)
  }
  if (anyDuplicated(group_cell_ids)) {
    duplicated <- sort(unique(group_cell_ids[duplicated(group_cell_ids)]))
    stop(sprintf("groups.tsv contains duplicated expression-column identifiers: %s", paste(duplicated, collapse = ", ")), call. = FALSE)
  }

  missing <- setdiff(cell_ids, group_cell_ids)
  extra <- setdiff(group_cell_ids, cell_ids)
  if (length(missing) > 0L || length(extra) > 0L) {
    details <- character()
    if (length(missing) > 0L) details <- c(details, paste0("missing expression columns: ", paste(missing, collapse = ", ")))
    if (length(extra) > 0L) details <- c(details, paste0("unknown expression columns: ", paste(extra, collapse = ", ")))
    stop(paste0("groups.tsv must match expression columns exactly (", paste(details, collapse = "; "), ")."), call. = FALSE)
  }

  cluster_values <- as.character(df[match(cell_ids, group_cell_ids), "cluster"])
  if (any(!nzchar(cluster_values))) {
    stop("groups.tsv contains empty cluster values.", call. = FALSE)
  }
}

empty_network <- function() {
  data.frame(
    source = character(),
    target = character(),
    score = numeric(),
    sign = character(),
    evidence = character(),
    context = character(),
    stringsAsFactors = FALSE
  )
}

capture_upstream_output <- function(log_path, expr, params, threads) {
  out_con <- file(log_path, open = "at")
  msg_con <- file(log_path, open = "at")
  sink(out_con, type = "output")
  sink(msg_con, type = "message")
  on.exit({
    sink(type = "message")
    sink(type = "output")
    close(msg_con)
    close(out_con)
  }, add = TRUE)

  run_kscreni_with_safe_pca(expr, params, threads, log_path)
}

run_kscreni_with_safe_pca <- function(expr, params, threads, log_path) {
  suppressPackageStartupMessages({
    suppressWarnings(library(Seurat))
    suppressWarnings(library(doParallel))
    suppressWarnings(library(foreach))
    suppressWarnings(library(GENIE3))
  })

  pbmc <- Seurat::CreateSeuratObject(counts = expr)
  all_genes <- rownames(pbmc)
  pbmc <- Seurat::NormalizeData(pbmc)
  pbmc <- Seurat::ScaleData(pbmc, features = all_genes)
  pbmc <- Seurat::FindVariableFeatures(
    pbmc,
    selection.method = "vst",
    nfeatures = params$nfeatures
  )

  variable_features <- Seurat::VariableFeatures(object = pbmc)
  safe_npcs <- min(50L, length(variable_features), ncol(expr) - 1L, nrow(expr) - 1L)
  if (safe_npcs < 1L) {
    stop("kScReNI requires at least one computable PCA component.", call. = FALSE)
  }
  append_log(
    log_path,
    sprintf(
      "Using npcs=%d for Seurat RunPCA to satisfy irlba strict rank bounds.",
      safe_npcs
    )
  )

  pbmc <- Seurat::RunPCA(pbmc, features = variable_features, npcs = safe_npcs)
  pbmc <- Seurat::FindNeighbors(pbmc, k.param = params$knn, features = variable_features)
  mat <- as.matrix(pbmc@graphs$RNA_snn)
  ncell <- ncol(expr)

  cl <- parallel::makeCluster(threads)
  doParallel::registerDoParallel(cl)
  on.exit({
    try(parallel::stopCluster(cl), silent = TRUE)
    foreach::registerDoSEQ()
  }, add = TRUE)

  sc_net_list <- foreach::foreach(
    i = seq_len(ncell),
    .combine = "c",
    .multicombine = TRUE
  ) %dopar% {
    snn_expr <- expr[, order(mat[i, ], decreasing = TRUE)[seq_len(params$knn + 1L)]]
    set.seed(100)
    sub_res <- GENIE3::GENIE3(snn_expr, nCores = 1, nTrees = 100, verbose = TRUE)
    sub_res <- ifelse(sub_res == "NaN", 0, sub_res)
    list(sub_res)
  }
  names(sc_net_list) <- colnames(expr)
  sc_net_list
}

coerce_weight_matrix <- function(value, cell_id) {
  mat <- as.matrix(value)
  suppressWarnings(storage.mode(mat) <- "double")
  if (!is.matrix(mat) || nrow(mat) < 1L || ncol(mat) < 1L) {
    stop(sprintf("kScReNI returned an empty matrix for cell %s.", cell_id), call. = FALSE)
  }
  if (is.null(rownames(mat)) || is.null(colnames(mat))) {
    stop(sprintf("kScReNI returned a matrix without gene dimnames for cell %s.", cell_id), call. = FALSE)
  }
  mat
}

network_to_andrea <- function(sc_networks, expected_cells) {
  if (!is.list(sc_networks)) {
    stop("kScReNI output must be a list of per-cell network matrices.", call. = FALSE)
  }
  if (length(sc_networks) != length(expected_cells)) {
    stop(
      sprintf(
        "kScReNI returned %d cell networks but expression.tsv has %d cells.",
        length(sc_networks),
        length(expected_cells)
      ),
      call. = FALSE
    )
  }

  network_names <- names(sc_networks)
  if (is.null(network_names) || any(!nzchar(network_names))) {
    network_names <- expected_cells
  }
  if (!identical(network_names, expected_cells)) {
    stop("kScReNI output cell names do not match expression.tsv cell identifiers.", call. = FALSE)
  }

  rows <- vector("list", length(sc_networks))
  for (i in seq_along(sc_networks)) {
    cell_id <- network_names[[i]]
    mat <- coerce_weight_matrix(sc_networks[[i]], cell_id)
    idx <- which(is.finite(mat) & mat > 0, arr.ind = TRUE)
    if (nrow(idx) == 0L) {
      rows[[i]] <- empty_network()
      next
    }

    sources <- rownames(mat)[idx[, 1L]]
    targets <- colnames(mat)[idx[, 2L]]
    scores <- as.numeric(mat[idx])
    keep <- sources != targets & is.finite(scores) & scores > 0
    if (!any(keep)) {
      rows[[i]] <- empty_network()
      next
    }

    rows[[i]] <- data.frame(
      source = sources[keep],
      target = targets[keep],
      score = scores[keep],
      sign = "?",
      evidence = "association",
      context = paste0("column:", cell_id),
      stringsAsFactors = FALSE
    )
  }

  out <- do.call(rbind, rows)
  if (is.null(out) || !nrow(out)) {
    return(empty_network())
  }

  out <- out[order(out$context, -out$score, out$source, out$target), , drop = FALSE]
  rownames(out) <- NULL
  out[, c("source", "target", "score", "sign", "evidence", "context"), drop = FALSE]
}

main <- function() {
  args <- parse_args()
  input_path <- args$input %||% stop("Missing required argument: --input", call. = FALSE)
  params_path <- args$params %||% stop("Missing required argument: --params", call. = FALSE)
  extra_dir <- args$extra %||% stop("Missing required argument: --extra", call. = FALSE)
  output_dir <- args$`output-dir` %||% stop("Missing required argument: --output-dir", call. = FALSE)
  threads_raw <- args$threads %||% stop("Missing required argument: --threads", call. = FALSE)

  threads <- suppressWarnings(as.integer(threads_raw))
  if (is.na(threads) || threads <= 0L) {
    stop("--threads must be a positive integer.", call. = FALSE)
  }

  if (!file.exists(input_path)) stop(sprintf("Input file not found: %s", input_path), call. = FALSE)
  if (!file.exists(params_path)) stop(sprintf("Params file not found: %s", params_path), call. = FALSE)
  if (!dir.exists(extra_dir)) stop(sprintf("Extra directory not found: %s", extra_dir), call. = FALSE)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  raw_dir <- file.path(output_dir, "raw")
  dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)

  progress_path <- file.path(output_dir, "progress.json")
  log_path <- file.path(output_dir, "kscreni.log")

  write_progress(progress_path, "running", 0L, "init", "Initializing kScReNI wrapper")
  append_log(log_path, "Initializing kScReNI wrapper")

  tryCatch({
    params <- resolve_params(load_params(params_path))
    mode <- load_execution_mode(params_path)

    write_progress(progress_path, "running", 5L, "load_input", "Loading expression and extra inputs")
    expr <- read_expression_tsv(input_path)
    validate_expression_for_params(expr, params)
    if (mode == "group_aggregated") {
      validate_groups(extra_dir, colnames(expr))
    }

    append_log(
      log_path,
      sprintf(
        "Loaded expression matrix: %d genes x %d cells; mode=%s; nfeatures=%d; knn=%d; threads=%d",
        nrow(expr),
        ncol(expr),
        mode,
        params$nfeatures,
        params$knn,
        threads
      )
    )

    write_progress(progress_path, "running", 15L, "inference", "Running kScReNI")
    sc_networks <- capture_upstream_output(log_path, expr, params, threads)
    saveRDS(sc_networks, file.path(raw_dir, "kscreni_networks.rds"))
    append_log(log_path, sprintf("kScReNI returned %d cell-specific networks", length(sc_networks)))

    write_progress(progress_path, "running", 90L, "write_output", "Writing network.csv")
    network_df <- network_to_andrea(sc_networks, colnames(expr))
    write.csv(network_df, file.path(output_dir, "network.csv"), row.names = FALSE)
    append_log(log_path, sprintf("Wrote network.csv with %d positive non-self edges", nrow(network_df)))

    write_progress(
      progress_path,
      "completed",
      100L,
      "done",
      "Inference finished",
      completed = nrow(network_df),
      total = nrow(network_df)
    )
  }, error = function(exc) {
    append_log(log_path, sprintf("ERROR: %s", conditionMessage(exc)))
    write_progress(
      progress_path,
      "failed",
      100L,
      "failed",
      "Inference failed",
      error = conditionMessage(exc)
    )
    stop(exc)
  })
}

main()
