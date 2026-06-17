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
  suppressWarnings(library(lionessR))
  suppressWarnings(library(SummarizedExperiment))
})

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

write_progress <- function(progress_path, status, percent, phase, message, error = NULL) {
  payload <- list(
    status = status,
    phase = phase,
    percent = max(0L, min(100L, as.integer(percent))),
    message = message,
    timestamp = as.numeric(Sys.time())
  )
  if (!is.null(error)) {
    payload$error <- as.character(error)
  }

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

load_params <- function(params_path) {
  params <- fromJSON(params_path, simplifyVector = TRUE)
  if (!is.list(params)) {
    stop("params.json must be a JSON object.", call. = FALSE)
  }
  params
}

resolve_params <- function(raw_params) {
  unexpected <- names(raw_params)
  if (length(unexpected) > 0L) {
    stop(sprintf(
      "LIONESS does not accept runtime params; unexpected params: %s",
      paste(sort(unexpected), collapse = ", ")
    ), call. = FALSE)
  }
  list()
}

read_expression_tsv <- function(expr_path) {
  df <- read.delim(
    expr_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(df) < 4L) {
    stop("expression.tsv must have one gene column and at least 3 cell/sample columns for LIONESS.", call. = FALSE)
  }

  gene_col <- names(df)[1L]
  genes <- trimws(as.character(df[[gene_col]]))
  if (any(!nzchar(genes))) {
    stop("expression.tsv contains an empty gene identifier.", call. = FALSE)
  }
  if (anyDuplicated(genes)) {
    duplicated <- sort(unique(genes[duplicated(genes)]))
    stop(sprintf("expression.tsv contains duplicated genes: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  cells <- trimws(names(df)[-1L])
  if (any(!nzchar(cells))) {
    stop("expression.tsv contains an empty cell/sample identifier in the header.", call. = FALSE)
  }
  if (anyDuplicated(cells)) {
    duplicated <- sort(unique(cells[duplicated(cells)]))
    stop(sprintf("expression.tsv contains duplicated cell/sample identifiers: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  num_df <- data.frame(
    lapply(df[, -1L, drop = FALSE], function(x) as.numeric(x)),
    check.names = FALSE
  )
  mat <- as.matrix(num_df)
  if (anyNA(mat) || any(!is.finite(mat))) {
    stop("expression.tsv contains non-finite or non-numeric expression values.", call. = FALSE)
  }

  rownames(mat) <- genes
  colnames(mat) <- cells
  mat
}

filter_variable_genes <- function(expr, log_path) {
  keep <- apply(expr, 1L, function(values) {
    sd_value <- stats::sd(values)
    is.finite(sd_value) && sd_value > 0
  })
  dropped <- rownames(expr)[!keep]
  if (length(dropped) > 0L) {
    append_log(log_path, sprintf("Filtered %d zero-variance gene(s) before LIONESS inference.", length(dropped)))
  }
  expr[keep, , drop = FALSE]
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

pick_column <- function(df, candidates) {
  lowered <- tolower(names(df))
  for (candidate in candidates) {
    idx <- match(tolower(candidate), lowered)
    if (!is.na(idx)) {
      return(names(df)[[idx]])
    }
  }
  NULL
}

extract_lioness_long <- function(result) {
  if (inherits(result, "SummarizedExperiment")) {
    assay_names <- SummarizedExperiment::assayNames(result)
    if (length(assay_names) < 1L) {
      stop("lionessR result does not contain any assays.", call. = FALSE)
    }
    assay_name <- if ("lioness" %in% assay_names) "lioness" else assay_names[[1L]]
    weights <- as.matrix(SummarizedExperiment::assay(result, assay_name))
    row_info <- as.data.frame(SummarizedExperiment::rowData(result), stringsAsFactors = FALSE)
    samples <- colnames(weights)
  } else if (is.data.frame(result)) {
    if (ncol(result) < 3L) {
      stop("lionessR data.frame result must have source, target, and >=1 sample columns.", call. = FALSE)
    }
    row_info <- result[, seq_len(2L), drop = FALSE]
    weights <- as.matrix(data.frame(
      lapply(result[, -(seq_len(2L)), drop = FALSE], function(x) as.numeric(x)),
      check.names = FALSE
    ))
    samples <- colnames(weights)
  } else {
    stop(sprintf("Unsupported lionessR result class: %s", paste(class(result), collapse = ", ")),
         call. = FALSE)
  }

  if (!is.matrix(weights) || nrow(weights) < 1L || ncol(weights) < 1L) {
    stop("lionessR returned an empty weight assay.", call. = FALSE)
  }
  if (!is.data.frame(row_info) || nrow(row_info) != nrow(weights)) {
    stop("lionessR rowData does not match the weight assay row count.", call. = FALSE)
  }
  if (is.null(samples) || any(!nzchar(samples))) {
    stop("lionessR result is missing sample names.", call. = FALSE)
  }

  source_col <- pick_column(row_info, c("reg", "regulator", "source", "from", "gene1"))
  target_col <- pick_column(row_info, c("tar", "target", "to", "gene2"))
  if (is.null(source_col) || is.null(target_col)) {
    if (ncol(row_info) < 2L) {
      stop("lionessR rowData must include source/regulator and target columns.", call. = FALSE)
    }
    source_col <- names(row_info)[[1L]]
    target_col <- names(row_info)[[2L]]
  }

  sources <- trimws(as.character(row_info[[source_col]]))
  targets <- trimws(as.character(row_info[[target_col]]))
  if (any(!nzchar(sources)) || any(!nzchar(targets))) {
    stop("lionessR returned empty source or target identifiers.", call. = FALSE)
  }

  data.frame(
    source = rep(sources, times = ncol(weights)),
    target = rep(targets, times = ncol(weights)),
    cell = rep(samples, each = nrow(weights)),
    weight = as.numeric(as.vector(weights)),
    stringsAsFactors = FALSE
  )
}

write_raw_weights <- function(long_weights, raw_path) {
  con <- gzfile(raw_path, open = "wt")
  on.exit(close(con), add = TRUE)
  write.table(
    long_weights,
    file = con,
    sep = "\t",
    row.names = FALSE,
    quote = FALSE
  )
}

build_network <- function(long_weights) {
  keep <- is.finite(long_weights$weight) & long_weights$source != long_weights$target
  long_weights <- long_weights[keep, , drop = FALSE]
  if (!nrow(long_weights)) {
    return(empty_network())
  }

  source <- ifelse(long_weights$source < long_weights$target, long_weights$source, long_weights$target)
  target <- ifelse(long_weights$source < long_weights$target, long_weights$target, long_weights$source)
  canonical <- data.frame(
    cell = long_weights$cell,
    source = source,
    target = target,
    weight = long_weights$weight,
    stringsAsFactors = FALSE
  )

  collapsed <- stats::aggregate(
    weight ~ cell + source + target,
    data = canonical,
    FUN = function(values) mean(as.numeric(values), na.rm = TRUE)
  )
  scores <- abs(collapsed$weight)
  keep <- is.finite(scores) & scores > 0
  if (!any(keep)) {
    return(empty_network())
  }
  collapsed <- collapsed[keep, , drop = FALSE]
  scores <- scores[keep]

  network <- data.frame(
    source = collapsed$source,
    target = collapsed$target,
    score = scores,
    sign = ifelse(collapsed$weight > 0, "+", "-"),
    evidence = "association",
    context = paste0("cell:", collapsed$cell),
    stringsAsFactors = FALSE
  )
  network[order(network$context, network$source, network$target), , drop = FALSE]
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
  if (threads != 1L) {
    stop(
      "LIONESS/lionessR exposes no upstream thread control; --threads must be 1.",
      call. = FALSE
    )
  }

  if (!file.exists(input_path)) stop(sprintf("Input file not found: %s", input_path), call. = FALSE)
  if (!file.exists(params_path)) stop(sprintf("Params file not found: %s", params_path), call. = FALSE)
  if (!dir.exists(extra_dir)) stop(sprintf("Extra directory not found: %s", extra_dir), call. = FALSE)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  raw_dir <- file.path(output_dir, "raw")
  dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
  progress_path <- file.path(output_dir, "progress.json")
  log_path <- file.path(output_dir, "lioness.log")

  append_log(log_path, "Initializing LIONESS wrapper")
  append_log(log_path, sprintf("lionessR version: %s", as.character(utils::packageVersion("lionessR"))))
  write_progress(progress_path, "running", 0L, "init", "Initializing LIONESS wrapper")

  tryCatch({
    raw_params <- load_params(params_path)
    resolve_params(raw_params)
    append_log(log_path, "Using fixed lionessR::netFun Pearson aggregate network function.")

    write_progress(progress_path, "running", 10L, "load_input", "Loading expression matrix")
    expression_data <- read_expression_tsv(input_path)
    expression_data <- filter_variable_genes(expression_data, log_path)
    if (nrow(expression_data) < 2L) {
      stop("LIONESS requires at least 2 variable genes after zero-variance filtering.", call. = FALSE)
    }
    if (ncol(expression_data) < 3L) {
      stop("LIONESS requires at least 3 cells/samples.", call. = FALSE)
    }
    append_log(log_path, sprintf(
      "Loaded expression matrix with %d variable genes and %d cells/samples.",
      nrow(expression_data),
      ncol(expression_data)
    ))

    write_progress(progress_path, "running", 30L, "inference", "Running lionessR::lioness")
    lioness_stdout <- capture.output(
      result <- lionessR::lioness(expression_data, lionessR::netFun)
    )
    if (length(lioness_stdout) > 0L) {
      append_log(log_path, paste(c("lionessR stdout:", lioness_stdout), collapse = "\n"))
    }
    saveRDS(result, file.path(raw_dir, "lioness_result.rds"))

    write_progress(progress_path, "running", 80L, "export", "Exporting LIONESS edge weights")
    long_weights <- extract_lioness_long(result)
    write_raw_weights(long_weights, file.path(raw_dir, "lioness_weights.tsv.gz"))
    network <- build_network(long_weights)
    write.csv(network, file.path(output_dir, "network.csv"), row.names = FALSE)
    append_log(log_path, sprintf("Wrote network.csv with %d non-zero undirected cell-context rows.", nrow(network)))
    append_log(log_path, paste(capture.output(sessionInfo()), collapse = "\n"))

    write_progress(progress_path, "completed", 100L, "done", "LIONESS completed successfully")
  }, error = function(exc) {
    append_log(log_path, sprintf("ERROR: %s", conditionMessage(exc)))
    append_log(log_path, paste(capture.output(sessionInfo()), collapse = "\n"))
    write_progress(
      progress_path,
      "failed",
      100L,
      "error",
      "LIONESS failed",
      error = conditionMessage(exc)
    )
    stop(exc)
  })
}

main()
