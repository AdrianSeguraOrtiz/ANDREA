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
  suppressWarnings(library(scMINER))
})

NETWORK_COLUMNS <- c("source", "target", "score", "sign", "evidence", "context")
SUPPORTED_MODES <- c("global", "group_emulated")
SUPPORTED_DRIVER_SOURCES <- c("built_in_tf_sig", "built_in_tf", "built_in_sig", "custom_tf_list")
SUPPORTED_SPECIES <- c("hg", "mm")
SJARACNE_BOOTSTRAP_PVALUE <- "0.0000001"

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

append_lines <- function(log_path, lines) {
  if (length(lines) == 0L) return(invisible(NULL))
  cat(paste0(lines, collapse = "\n"), "\n", file = log_path, append = TRUE)
}

is_scalar_string <- function(x) is.character(x) && length(x) == 1L && !is.na(x)
is_scalar_number <- function(x) is.numeric(x) && length(x) == 1L && !is.na(x)

as_enum <- function(name, value, allowed) {
  if (!is_scalar_string(value) || !(value %in% allowed)) {
    stop(sprintf("%s must be one of: %s.", name, paste(allowed, collapse = ", ")), call. = FALSE)
  }
  value
}

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

as_float_checked <- function(name, value, min_value = NULL, max_value = NULL, exclusive_min = FALSE) {
  if (!is_scalar_number(value) || !is.finite(value)) {
    stop(sprintf("%s must be a finite number.", name), call. = FALSE)
  }
  out <- as.numeric(value)
  if (!is.null(min_value)) {
    if (exclusive_min && out <= min_value) {
      stop(sprintf("%s must be > %s.", name, min_value), call. = FALSE)
    }
    if (!exclusive_min && out < min_value) {
      stop(sprintf("%s must be >= %s.", name, min_value), call. = FALSE)
    }
  }
  if (!is.null(max_value) && out > max_value) {
    stop(sprintf("%s must be <= %s.", name, max_value), call. = FALSE)
  }
  out
}

load_json_object <- function(path, label) {
  payload <- fromJSON(path, simplifyVector = TRUE)
  if (!is.list(payload)) {
    stop(sprintf("%s must be a JSON object.", label), call. = FALSE)
  }
  payload
}

resolve_params <- function(raw_params) {
  expected <- c(
    "driver_source",
    "species_type",
    "downSample_N",
    "n_bootstraps",
    "consensus_pvalue",
    "random_seed"
  )
  unknown <- setdiff(names(raw_params), expected)
  if (length(unknown) > 0L) {
    warning(sprintf("Unknown params ignored: %s", paste(sort(unknown), collapse = ", ")))
  }

  down_sample <- 1000L
  if ("downSample_N" %in% names(raw_params)) {
    raw_down_sample <- raw_params[["downSample_N"]]
    if (is.null(raw_down_sample)) {
      down_sample <- NULL
    } else {
      down_sample <- as_int_checked("downSample_N", raw_down_sample, min_value = 2L)
    }
  }

  list(
    driver_source = as_enum(
      "driver_source",
      raw_params$driver_source %||% "built_in_tf_sig",
      SUPPORTED_DRIVER_SOURCES
    ),
    species_type = as_enum("species_type", raw_params$species_type %||% "hg", SUPPORTED_SPECIES),
    downSample_N = down_sample,
    n_bootstraps = as_int_checked("n_bootstraps", raw_params$n_bootstraps %||% 100, min_value = 1L),
    consensus_pvalue = as_float_checked(
      "consensus_pvalue",
      raw_params$consensus_pvalue %||% 0.01,
      min_value = 0,
      max_value = 1,
      exclusive_min = TRUE
    ),
    random_seed = as_int_checked("random_seed", raw_params$random_seed %||% 123, min_value = 0L)
  )
}

load_execution_mode <- function(params_path) {
  execution_path <- file.path(dirname(params_path), "execution.json")
  if (!file.exists(execution_path)) {
    return("global")
  }
  execution <- load_json_object(execution_path, "execution.json")
  mode <- execution$mode %||% "global"
  if (!is_scalar_string(mode) || !(mode %in% SUPPORTED_MODES)) {
    stop(
      sprintf("scMINER supports only execution.mode values: %s.", paste(SUPPORTED_MODES, collapse = ", ")),
      call. = FALSE
    )
  }
  mode
}

read_expression_tsv <- function(expr_path) {
  df <- read.delim(
    expr_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(df) < 3L) {
    stop("expression.tsv must have one gene column and at least two expression columns.", call. = FALSE)
  }

  gene_ids <- as.character(df[[1L]])
  if (any(!nzchar(gene_ids))) {
    stop("expression.tsv contains an empty gene identifier.", call. = FALSE)
  }
  if (anyDuplicated(gene_ids)) {
    duplicated <- sort(unique(gene_ids[duplicated(gene_ids)]))
    stop(sprintf("expression.tsv contains duplicated genes: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  expression_columns <- as.character(names(df)[-1L])
  if (any(!nzchar(expression_columns))) {
    stop("expression.tsv contains an empty expression column identifier.", call. = FALSE)
  }
  if (anyDuplicated(expression_columns)) {
    duplicated <- sort(unique(expression_columns[duplicated(expression_columns)]))
    stop(sprintf(
      "expression.tsv contains duplicated expression columns: %s",
      paste(duplicated, collapse = ", ")
    ), call. = FALSE)
  }

  num_df <- data.frame(
    lapply(df[, -1L, drop = FALSE], function(x) suppressWarnings(as.numeric(x))),
    check.names = FALSE
  )
  mat <- as.matrix(num_df)
  if (anyNA(mat) || any(!is.finite(mat))) {
    stop("expression.tsv contains non-finite or non-numeric expression values.", call. = FALSE)
  }

  rownames(mat) <- gene_ids
  colnames(mat) <- expression_columns
  mat
}

read_groups <- function(extra_dir, expression_columns) {
  groups_path <- file.path(extra_dir, "groups.tsv")
  if (!file.exists(groups_path)) {
    stop("groups.tsv is required when execution.mode=group_emulated.", call. = FALSE)
  }
  groups_df <- read.delim(
    groups_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(groups_df) < 2L || !("cluster" %in% names(groups_df))) {
    stop("groups.tsv must contain an expression-column id column and a cluster column.", call. = FALSE)
  }

  column_ids <- as.character(groups_df[[1L]])
  clusters <- as.character(groups_df[["cluster"]])
  if (any(!nzchar(column_ids))) {
    stop("groups.tsv contains an empty expression-column identifier.", call. = FALSE)
  }
  if (any(!nzchar(clusters))) {
    stop("groups.tsv contains an empty cluster value.", call. = FALSE)
  }
  if (anyDuplicated(column_ids)) {
    duplicated <- sort(unique(column_ids[duplicated(column_ids)]))
    stop(sprintf("groups.tsv contains duplicated expression-column ids: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  group_map <- stats::setNames(clusters, column_ids)
  missing <- setdiff(expression_columns, names(group_map))
  if (length(missing) > 0L) {
    stop(paste0("groups.tsv is missing expression columns: ", paste(head(missing, 8L), collapse = ", ")),
         call. = FALSE)
  }

  group_map[expression_columns]
}

safe_slug <- function(value, used) {
  slug <- gsub("[^A-Za-z0-9_.-]+", "_", value)
  slug <- gsub("^_+|_+$", "", slug)
  if (!nzchar(slug)) slug <- "context"
  candidate <- slug
  idx <- 2L
  while (candidate %in% used) {
    candidate <- paste0(slug, "_", idx)
    idx <- idx + 1L
  }
  candidate
}

make_gene_aliases <- function(gene_ids) {
  aliases <- sprintf("g%06d", seq_along(gene_ids))
  data.frame(
    gene_id = gene_ids,
    upstream_id = aliases,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
}

write_alias_map <- function(alias_df, path) {
  write.table(alias_df, file = path, sep = "\t", row.names = FALSE, quote = FALSE)
}

write_sjaracne_expression <- function(expr, alias_df, path) {
  aliases <- alias_df$upstream_id[match(rownames(expr), alias_df$gene_id)]
  if (anyNA(aliases)) {
    stop("Internal error: missing gene alias while writing SJARACNe expression.", call. = FALSE)
  }
  out <- data.frame(
    isoformId = aliases,
    geneSymbol = rownames(expr),
    as.data.frame(expr, check.names = FALSE),
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  write.table(out, file = path, sep = "\t", row.names = FALSE, quote = FALSE, col.names = TRUE)
}

write_driver_file <- function(driver_genes, alias_df, path) {
  aliases <- alias_df$upstream_id[match(driver_genes, alias_df$gene_id)]
  if (anyNA(aliases)) {
    stop("Internal error: missing gene alias while writing SJARACNe drivers.", call. = FALSE)
  }
  writeLines(aliases, path, useBytes = TRUE)
}

read_tf_list <- function(extra_dir) {
  tf_path <- file.path(extra_dir, "tf_list.txt")
  if (!file.exists(tf_path)) {
    stop("tf_list.txt is required when driver_source=custom_tf_list.", call. = FALSE)
  }
  values <- trimws(readLines(tf_path, warn = FALSE))
  values <- values[nzchar(values)]
  values <- unique(values)
  if (length(values) == 0L) {
    stop("tf_list.txt contains no non-empty TF identifiers.", call. = FALSE)
  }
  values
}

resolve_driver_sets <- function(params, expr_gene_ids, extra_dir) {
  if (params$driver_source == "custom_tf_list") {
    requested <- read_tf_list(extra_dir)
    matched <- requested[requested %in% expr_gene_ids]
    if (length(matched) == 0L) {
      stop("No tf_list.txt entries match expression gene ids.", call. = FALSE)
    }
    return(list(TF = matched))
  }

  driver_types <- switch(
    params$driver_source,
    built_in_tf_sig = c("TF", "SIG"),
    built_in_tf = "TF",
    built_in_sig = "SIG"
  )

  out <- list()
  for (driver_type in driver_types) {
    drivers <- scMINER::getDriverList(species_type = params$species_type, driver_type = driver_type)
    matched <- unique(as.character(drivers[drivers %in% expr_gene_ids]))
    if (length(matched) == 0L) {
      stop(
        sprintf(
          "scMINER built-in %s driver list for species_type=%s has no overlap with expression gene ids.",
          driver_type,
          params$species_type
        ),
        call. = FALSE
      )
    }
    out[[driver_type]] <- matched
  }
  out
}

prepare_context_expression <- function(expr, params) {
  if (ncol(expr) < 2L) {
    stop("scMINER/SJARACNe requires at least two expression columns in each run context.", call. = FALSE)
  }
  if (nrow(expr) < 2L) {
    stop("scMINER/SJARACNe requires at least two genes in each run context.", call. = FALSE)
  }

  if (!is.null(params$downSample_N) && ncol(expr) > params$downSample_N) {
    set.seed(params$random_seed)
    selected <- sample(seq_len(ncol(expr)), size = params$downSample_N, replace = FALSE)
    expr <- expr[, selected, drop = FALSE]
  }
  if (ncol(expr) < 2L) {
    stop("downSample_N leaves fewer than two expression columns for SJARACNe.", call. = FALSE)
  }

  keep <- rowSums(as.matrix(expr), na.rm = TRUE) > 0
  expr <- expr[keep, , drop = FALSE]
  if (nrow(expr) < 2L) {
    stop("Fewer than two genes remain after removing all-zero rows for SJARACNe.", call. = FALSE)
  }
  expr
}

run_sjaracne <- function(exp_path, driver_path, output_dir, params, log_path) {
  args <- c(
    "local",
    "--serial",
    "-e", exp_path,
    "-g", driver_path,
    "-o", output_dir,
    "-n", as.character(params$n_bootstraps),
    "-pc", format(params$consensus_pvalue, scientific = FALSE, trim = TRUE),
    "-pb", SJARACNE_BOOTSTRAP_PVALUE
  )
  append_log(log_path, paste("running:", "sjaracne", paste(shQuote(args), collapse = " ")))
  result <- system2("sjaracne", args = args, stdout = TRUE, stderr = TRUE)
  append_lines(log_path, result)
  status <- attr(result, "status")
  if (!is.null(status) && status != 0L) {
    stop(sprintf("sjaracne local failed with exit code %s.", status), call. = FALSE)
  }
  network_path <- file.path(output_dir, "consensus_network_ncol_.txt")
  if (!file.exists(network_path) || file.info(network_path)$size <= 0) {
    stop(sprintf("SJARACNe did not produce a non-empty consensus network: %s", network_path), call. = FALSE)
  }
  network_path
}

map_alias <- function(value, alias_lookup, symbol_value = NA_character_) {
  mapped <- unname(alias_lookup[[as.character(value)]])
  if (!is.null(mapped) && !is.na(mapped)) return(mapped)
  if (!is.na(symbol_value) && nzchar(symbol_value)) return(as.character(symbol_value))
  NA_character_
}

parse_sjaracne_network <- function(network_path, context, alias_df) {
  raw <- read.delim(network_path, sep = "\t", header = TRUE, check.names = FALSE, stringsAsFactors = FALSE)
  required <- c("source", "target", "MI", "spearman")
  missing <- setdiff(required, names(raw))
  if (length(missing) > 0L) {
    stop(sprintf("SJARACNe network is missing columns: %s", paste(missing, collapse = ", ")), call. = FALSE)
  }

  alias_lookup <- stats::setNames(alias_df$gene_id, alias_df$upstream_id)
  source_symbol_col <- if ("source.symbol" %in% names(raw)) raw[["source.symbol"]] else rep(NA_character_, nrow(raw))
  target_symbol_col <- if ("target.symbol" %in% names(raw)) raw[["target.symbol"]] else rep(NA_character_, nrow(raw))

  rows <- list()
  for (idx in seq_len(nrow(raw))) {
    score <- suppressWarnings(as.numeric(raw[["MI"]][idx]))
    if (!is.finite(score) || score <= 0) next

    source <- map_alias(raw[["source"]][idx], alias_lookup, source_symbol_col[idx])
    target <- map_alias(raw[["target"]][idx], alias_lookup, target_symbol_col[idx])
    if (is.na(source) || is.na(target) || !nzchar(source) || !nzchar(target)) next
    if (identical(source, target)) next

    spearman <- suppressWarnings(as.numeric(raw[["spearman"]][idx]))
    sign_value <- "?"
    if (is.finite(spearman) && spearman > 0) {
      sign_value <- "+"
    } else if (is.finite(spearman) && spearman < 0) {
      sign_value <- "-"
    }

    rows[[length(rows) + 1L]] <- data.frame(
      source = source,
      target = target,
      score = score,
      sign = sign_value,
      evidence = "association",
      context = context,
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  }

  if (!length(rows)) {
    return(data.frame(
      source = character(),
      target = character(),
      score = numeric(),
      sign = character(),
      evidence = character(),
      context = character(),
      stringsAsFactors = FALSE,
      check.names = FALSE
    ))
  }

  do.call(rbind, rows)[, NETWORK_COLUMNS, drop = FALSE]
}

deduplicate_edges <- function(network_df) {
  if (nrow(network_df) == 0L) return(network_df)
  ord <- order(network_df$context, network_df$source, network_df$target, -network_df$score, network_df$sign)
  network_df <- network_df[ord, , drop = FALSE]
  key <- paste(network_df$context, network_df$source, network_df$target, sep = "\r")
  network_df <- network_df[!duplicated(key), , drop = FALSE]
  network_df <- network_df[order(network_df$context, -network_df$score, network_df$source, network_df$target), , drop = FALSE]
  rownames(network_df) <- NULL
  network_df[, NETWORK_COLUMNS, drop = FALSE]
}

run_context <- function(context_label, expr, params, extra_dir, alias_df, input_dir, output_root, log_path, used_slugs) {
  expr_prepared <- prepare_context_expression(expr, params)
  driver_sets <- resolve_driver_sets(params, rownames(expr_prepared), extra_dir)

  context_slug <- safe_slug(context_label, used_slugs)
  used_slugs <- c(used_slugs, context_slug)

  edges <- list()
  for (driver_type in names(driver_sets)) {
    run_slug <- safe_slug(paste(context_slug, driver_type, sep = "_"), character())
    exp_path <- file.path(input_dir, paste0(run_slug, ".exp.txt"))
    driver_path <- file.path(input_dir, paste0(run_slug, ".drivers.txt"))
    sjaracne_output_dir <- file.path(output_root, run_slug)

    write_sjaracne_expression(expr_prepared, alias_df, exp_path)
    write_driver_file(driver_sets[[driver_type]], alias_df, driver_path)

    append_log(
      log_path,
      sprintf(
        "context=%s driver_type=%s genes=%d columns=%d drivers=%d",
        context_label,
        driver_type,
        nrow(expr_prepared),
        ncol(expr_prepared),
        length(driver_sets[[driver_type]])
      )
    )

    network_path <- run_sjaracne(exp_path, driver_path, sjaracne_output_dir, params, log_path)
    edges[[length(edges) + 1L]] <- parse_sjaracne_network(network_path, context_label, alias_df)
  }

  list(edges = edges, used_slugs = used_slugs)
}

get_sjaracne_version <- function() {
  result <- tryCatch(
    system2("python3", c("-m", "pip", "show", "SJARACNe"), stdout = TRUE, stderr = TRUE),
    error = function(exc) "unknown"
  )
  version_line <- grep("^Version:", result, value = TRUE)
  if (length(version_line) == 0L) {
    return("unknown")
  }
  trimws(sub("^Version:", "", version_line[[1L]]))
}

write_config <- function(path, params, expression_data, execution_mode, group_map, threads, exported_edges) {
  config <- list(
    tool = "scminer",
    upstream_packages = list(
      scMINER = as.character(utils::packageVersion("scMINER")),
      SJARACNe = get_sjaracne_version()
    ),
    upstream_refs = list(
      scMINER = "48d4bc4e7fe7ccc880e5816857f264eb7c2161e9",
      SJARACNe = "0.2.1"
    ),
    entrypoint = "scMINER-style SJARACNe input preparation plus sjaracne local --serial",
    execution_mode = execution_mode,
    gene_count = nrow(expression_data),
    expression_column_count = ncol(expression_data),
    group_count = if (is.null(group_map)) NULL else length(unique(unname(group_map))),
    requested_threads = threads,
    upstream_threads = 1L,
    params = params,
    fixed_upstream = list(
      sjaracne_mode = "local",
      sjaracne_serial = TRUE,
      p_value_bootstrap = SJARACNE_BOOTSTRAP_PVALUE,
      superCell_N = NULL
    ),
    exported_edges = exported_edges,
    score_rule = "score=SJARACNe MI; sign=sign(SJARACNe spearman); zero and non-positive MI edges omitted"
  )
  writeLines(toJSON(config, auto_unbox = TRUE, null = "null", pretty = TRUE), path, useBytes = TRUE)
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
    stop("scMINER/SJARACNe exposes no bounded upstream thread control in this wrapper; --threads must be 1.", call. = FALSE)
  }

  if (!file.exists(input_path)) stop(sprintf("Input file not found: %s", input_path), call. = FALSE)
  if (!file.exists(params_path)) stop(sprintf("Params file not found: %s", params_path), call. = FALSE)
  if (!dir.exists(extra_dir)) stop(sprintf("Extra directory not found: %s", extra_dir), call. = FALSE)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  raw_dir <- file.path(output_dir, "raw")
  input_dir <- file.path(raw_dir, "sjaracne_inputs")
  sjaracne_output_root <- file.path(raw_dir, "sjaracne_outputs")
  dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(input_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(sjaracne_output_root, recursive = TRUE, showWarnings = FALSE)

  progress_path <- file.path(output_dir, "progress.json")
  log_path <- file.path(output_dir, "scminer.log")
  network_path <- file.path(output_dir, "network.csv")

  write_progress(progress_path, "running", 0L, "init", "Initializing scMINER wrapper")
  append_log(log_path, "scMINER wrapper starting")

  tryCatch({
    raw_params <- load_json_object(params_path, "params.json")
    params <- resolve_params(raw_params)
    execution_mode <- load_execution_mode(params_path)

    write_progress(progress_path, "running", 10L, "load_input", "Loading expression matrix")
    expression_data <- read_expression_tsv(input_path)
    group_map <- NULL
    if (execution_mode == "group_emulated") {
      group_map <- read_groups(extra_dir, colnames(expression_data))
    }

    alias_df <- make_gene_aliases(rownames(expression_data))
    write_alias_map(alias_df, file.path(raw_dir, "gene_alias_map.tsv"))

    write_progress(progress_path, "running", 30L, "inference", "Running SJARACNe")
    all_edges <- list()
    used_slugs <- character()

    if (execution_mode == "global") {
      result <- run_context(
        "global",
        expression_data,
        params,
        extra_dir,
        alias_df,
        input_dir,
        sjaracne_output_root,
        log_path,
        used_slugs
      )
      all_edges <- c(all_edges, result$edges)
    } else {
      groups <- unique(unname(group_map))
      total <- length(groups)
      for (idx in seq_along(groups)) {
        group_id <- groups[[idx]]
        write_progress(
          progress_path,
          "running",
          30L + floor(50L * (idx - 1L) / max(total, 1L)),
          "inference",
          sprintf("Running SJARACNe for group %s", group_id)
        )
        cols <- names(group_map)[unname(group_map) == group_id]
        result <- run_context(
          paste0("group:", group_id),
          expression_data[, cols, drop = FALSE],
          params,
          extra_dir,
          alias_df,
          input_dir,
          sjaracne_output_root,
          log_path,
          used_slugs
        )
        all_edges <- c(all_edges, result$edges)
        used_slugs <- result$used_slugs
      }
    }

    write_progress(progress_path, "running", 85L, "write_output", "Converting SJARACNe output")
    if (!length(all_edges)) {
      stop("scMINER/SJARACNe produced no edge tables.", call. = FALSE)
    }
    network_df <- deduplicate_edges(do.call(rbind, all_edges))
    if (nrow(network_df) == 0L) {
      stop("scMINER/SJARACNe produced no positive MI edges.", call. = FALSE)
    }

    write.csv(network_df, network_path, row.names = FALSE)
    write_config(
      file.path(raw_dir, "scminer_config.json"),
      params,
      expression_data,
      execution_mode,
      group_map,
      threads,
      nrow(network_df)
    )

    append_log(log_path, sprintf("scMINER completed with %d exported edges", nrow(network_df)))
    write_progress(progress_path, "completed", 100L, "done", "scMINER completed successfully")
  }, error = function(exc) {
    append_log(log_path, sprintf("wrapper failure: %s", conditionMessage(exc)))
    write_progress(
      progress_path,
      "failed",
      100L,
      "error",
      "scMINER failed",
      error = conditionMessage(exc)
    )
    stop(exc)
  })
}

main()
