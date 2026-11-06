#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
contract_test <- length(args) == 3 && identical(args[[3]], "--contract-test")
if (!(length(args) == 2 || contract_test)) {
  stop("Usage: Rscript analysis/mixed_effects.R prepared-directory output.json [--contract-test]", call. = FALSE)
}

prepared_dir <- normalizePath(args[[1]], mustWork = TRUE)
output_path <- normalizePath(args[[2]], mustWork = FALSE)
if (file.exists(output_path)) {
  stop(paste("refusing to overwrite", output_path), call. = FALSE)
}

required_packages <- c("lme4", "emmeans", "jsonlite", "digest")
missing_packages <- required_packages[!vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing_packages) > 0) {
  stop(paste("missing required R packages:", paste(missing_packages, collapse = ", ")), call. = FALSE)
}

manifest_path <- file.path(prepared_dir, "analysis-manifest.json")
spec_path <- file.path(prepared_dir, "model-spec.snapshot.json")
data_path <- file.path(prepared_dir, "episodes.csv")
manifest <- jsonlite::read_json(manifest_path, simplifyVector = TRUE)
spec <- jsonlite::read_json(spec_path, simplifyVector = TRUE)

if (!identical(manifest$schemaVersion, "mixed-effects-input-1.0")) {
  stop("prepared input schema is invalid", call. = FALSE)
}
if (contract_test) {
  if (!isTRUE(manifest$contractTest) || isTRUE(manifest$formalAnalysisReady)) {
    stop("contract-test mode requires an explicitly non-formal prepared fixture", call. = FALSE)
  }
} else {
  if (!isTRUE(manifest$formalAnalysisReady) || isTRUE(manifest$contractTest)) {
    stop("prepared input is not formal-analysis ready", call. = FALSE)
  }
  if (!identical(spec$status, "frozen") || !identical(spec$runtime$status, "frozen-tested")) {
    stop("model specification or statistical runtime is not frozen", call. = FALSE)
  }
  if (is.null(spec$runtime$lockedVersions)) {
    stop("frozen model specification has no locked R/package versions", call. = FALSE)
  }
}

sha256_file <- function(path) digest::digest(path, algo = "sha256", file = TRUE)
if (!identical(sha256_file(spec_path), manifest$modelSpecSha256) ||
    !identical(sha256_file(spec_path), manifest$files$modelSpecSnapshot$sha256)) {
  stop("model specification snapshot hash mismatch", call. = FALSE)
}
if (!identical(sha256_file(data_path), manifest$files$episodesCsv$sha256)) {
  stop("prepared episode table hash mismatch", call. = FALSE)
}
full_args <- commandArgs(trailingOnly = FALSE)
script_arguments <- sub("^--file=", "", full_args[grepl("^--file=", full_args)])
if (length(script_arguments) != 1) {
  stop("cannot identify the running mixed-effects script", call. = FALSE)
}
running_script <- normalizePath(script_arguments[[1]], mustWork = TRUE)
if (!identical(sha256_file(running_script), manifest$provenance$analysisContract$modelScriptHash)) {
  stop("running mixed-effects script does not match the frozen analysis contract", call. = FALSE)
}

current_versions <- c(
  R = paste(R.version$major, R.version$minor, sep = "."),
  lme4 = as.character(utils::packageVersion("lme4")),
  emmeans = as.character(utils::packageVersion("emmeans")),
  jsonlite = as.character(utils::packageVersion("jsonlite")),
  digest = as.character(utils::packageVersion("digest"))
)
if (!contract_test) {
  locked_versions <- unlist(spec$runtime$lockedVersions, use.names = TRUE)
  if (!setequal(names(current_versions), names(locked_versions)) ||
      any(current_versions[names(locked_versions)] != locked_versions)) {
    stop(
      paste0(
        "R runtime does not match the frozen lock; current=",
        paste(names(current_versions), current_versions, sep = ":", collapse = ","),
        "; locked=",
        paste(names(locked_versions), locked_versions, sep = ":", collapse = ",")
      ),
      call. = FALSE
    )
  }
}

data <- utils::read.csv(data_path, stringsAsFactors = FALSE, check.names = FALSE, na.strings = c(""))
required_columns <- c(
  "run_id", "block_id", "model_key", "method", "template_id",
  "interaction_length", "execution_position", "success", "technical_failure"
)
if (!all(required_columns %in% names(data)) || nrow(data) != manifest$designAudit$episodes) {
  stop("prepared episode table does not match its design audit", call. = FALSE)
}

data$method <- factor(data$method, levels = unlist(spec$model$methodLevels))
data$interaction_length <- factor(
  as.character(data$interaction_length),
  levels = unlist(spec$model$lengthLevels)
)
data$execution_position <- factor(
  as.character(data$execution_position),
  levels = unlist(spec$model$executionPositionLevels)
)
data$template_id <- factor(data$template_id)
data$block_id <- factor(data$block_id)
data$model_key <- factor(data$model_key)
data$success <- as.integer(data$success)
if (anyNA(data[c("method", "interaction_length", "execution_position", "success")]) ||
    any(!data$success %in% c(0L, 1L))) {
  stop("prepared episode table contains invalid registered factors or outcomes", call. = FALSE)
}

alpha <- as.numeric(spec$confirmatory$alpha)
confidence_level <- as.numeric(spec$confirmatory$confidenceLevel)
target_length <- as.character(spec$confirmatory$primaryEndpoint$targetInteractionLength)
primary_contrasts <- unlist(spec$confirmatory$primaryContrasts)
contrast_vectors <- list(
  "D-A" = c(-1, 0, 0, 1),
  "D-B" = c(0, -1, 0, 1),
  "D-C" = c(0, 0, -1, 1)
)
if (!identical(primary_contrasts, names(contrast_vectors))) {
  stop("the frozen primary contrasts are not implemented by this script", call. = FALSE)
}

as_records <- function(frame) {
  if (is.null(frame) || nrow(frame) == 0) return(list())
  frame[] <- lapply(frame, function(column) {
    if (is.factor(column)) as.character(column) else column
  })
  unname(split(frame, seq_len(nrow(frame))))
}

fit_with_warnings <- function(formula, subset) {
  warnings <- character()
  fit <- withCallingHandlers(
    tryCatch(
      lme4::glmer(
        formula,
        data = subset,
        family = stats::binomial(link = "logit"),
        nAGQ = 1,
        control = lme4::glmerControl(
          optimizer = spec$model$optimizer,
          optCtrl = list(maxfun = as.integer(spec$model$maxfun)),
          calc.derivs = TRUE
        )
      ),
      error = function(error) error
    ),
    warning = function(warning) {
      warnings <<- c(warnings, conditionMessage(warning))
      invokeRestart("muffleWarning")
    }
  )
  list(fit = fit, warnings = unique(warnings))
}

fit_diagnostics <- function(fit, warnings) {
  convergence_messages <- fit@optinfo$conv$lme4$messages
  gradient <- fit@optinfo$derivs$gradient
  list(
    warnings = warnings,
    optimizerCode = fit@optinfo$conv$opt,
    convergenceMessages = if (is.null(convergence_messages)) list() else as.list(convergence_messages),
    singular = lme4::isSingular(fit, tol = 1e-4),
    maxAbsoluteGradient = if (is.null(gradient)) NULL else max(abs(gradient)),
    randomEffects = as_records(as.data.frame(lme4::VarCorr(fit)))
  )
}

cell_outcomes <- function(subset) {
  result <- list()
  for (method in levels(subset$method)) {
    result[[method]] <- list()
    for (length_value in levels(subset$interaction_length)) {
      values <- subset$success[subset$method == method & subset$interaction_length == length_value]
      result[[method]][[length_value]] <- list(
        episodes = length(values),
        successes = sum(values),
        failures = length(values) - sum(values),
        boundary = length(values) > 0 && (all(values == 0) || all(values == 1))
      )
    }
  }
  result
}

fixed_effects <- function(fit) {
  table <- as.data.frame(stats::coef(summary(fit)))
  table$term <- rownames(table)
  rownames(table) <- NULL
  names(table)[1:4] <- c("estimate", "standardError", "z", "p")
  critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
  table$oddsRatio <- exp(table$estimate)
  table$oddsRatioLower <- exp(table$estimate - critical * table$standardError)
  table$oddsRatioUpper <- exp(table$estimate + critical * table$standardError)
  table[, c("term", "estimate", "standardError", "z", "p", "oddsRatio", "oddsRatioLower", "oddsRatioUpper")]
}

contrast_tables <- function(fit) {
  means <- emmeans::emmeans(fit, ~ method | interaction_length)
  link <- as.data.frame(summary(
    emmeans::contrast(means, method = contrast_vectors, by = "interaction_length", adjust = "none"),
    infer = c(TRUE, TRUE), level = confidence_level, type = "response"
  ))
  probability_means <- emmeans::regrid(means, transform = "response")
  probability <- as.data.frame(summary(
    emmeans::contrast(probability_means, method = contrast_vectors, by = "interaction_length", adjust = "none"),
    infer = c(TRUE, TRUE), level = confidence_level
  ))
  primary <- as.character(link$interaction_length) == target_length
  link$primary <- primary
  link$holmAdjustedP <- NA_real_
  link$holmAdjustedP[primary] <- stats::p.adjust(link$p.value[primary], method = "holm")
  link$rejectAtAlpha <- !is.na(link$holmAdjustedP) & link$holmAdjustedP < alpha
  probability$primary <- as.character(probability$interaction_length) == target_length
  list(oddsRatios = link, probabilityDifferences = probability)
}

interaction_test <- function(fit) {
  reduced <- tryCatch(stats::update(fit, . ~ . - method:interaction_length), error = function(error) error)
  if (inherits(reduced, "error")) return(list(error = conditionMessage(reduced)))
  comparison <- tryCatch(stats::anova(reduced, fit, test = "Chisq"), error = function(error) error)
  if (inherits(comparison, "error")) return(list(error = conditionMessage(comparison)))
  as_records(as.data.frame(comparison))
}

fit_one_model <- function(model_key) {
  subset <- droplevels(data[data$model_key == model_key, , drop = FALSE])
  formula <- stats::as.formula(spec$model$formulaR)
  attempted <- fit_with_warnings(formula, subset)
  if (inherits(attempted$fit, "error")) {
    return(list(
      modelKey = model_key,
      estimable = FALSE,
      error = conditionMessage(attempted$fit),
      warnings = attempted$warnings,
      outcomeCounts = cell_outcomes(subset)
    ))
  }
  fit <- attempted$fit
  diagnostics <- fit_diagnostics(fit, attempted$warnings)
  contrasts <- tryCatch(contrast_tables(fit), error = function(error) error)
  contrast_error <- if (inherits(contrasts, "error")) conditionMessage(contrasts) else NULL
  sensitivity_formula <- stats::as.formula(
    "success ~ method * interaction_length + execution_position + template_id + (1 | block_id)"
  )
  sensitivity_attempted <- fit_with_warnings(sensitivity_formula, subset)
  sensitivity <- if (inherits(sensitivity_attempted$fit, "error")) {
    list(estimable = FALSE, error = conditionMessage(sensitivity_attempted$fit), warnings = sensitivity_attempted$warnings)
  } else {
    sensitivity_contrasts <- tryCatch(contrast_tables(sensitivity_attempted$fit), error = function(error) error)
    list(
      estimable = !inherits(sensitivity_contrasts, "error"),
      diagnostics = fit_diagnostics(sensitivity_attempted$fit, sensitivity_attempted$warnings),
      contrasts = if (inherits(sensitivity_contrasts, "error")) NULL else list(
        oddsRatios = as_records(sensitivity_contrasts$oddsRatios),
        probabilityDifferences = as_records(sensitivity_contrasts$probabilityDifferences)
      ),
      error = if (inherits(sensitivity_contrasts, "error")) conditionMessage(sensitivity_contrasts) else NULL
    )
  }
  inference_ready <- is.null(contrast_error) &&
    !diagnostics$singular &&
    length(diagnostics$convergenceMessages) == 0 &&
    isTRUE(diagnostics$optimizerCode == 0)
  list(
    modelKey = model_key,
    estimable = is.null(contrast_error),
    confirmatoryInferenceReady = inference_ready,
    outcomeCounts = cell_outcomes(subset),
    diagnostics = diagnostics,
    fixedEffects = as_records(fixed_effects(fit)),
    contrasts = if (is.null(contrast_error)) list(
      oddsRatios = as_records(contrasts$oddsRatios),
      probabilityDifferences = as_records(contrasts$probabilityDifferences)
    ) else NULL,
    contrastError = contrast_error,
    methodByLengthLikelihoodRatio = interaction_test(fit),
    templateFixedEffectSensitivity = sensitivity
  )
}

model_keys <- levels(data$model_key)
results <- lapply(model_keys, fit_one_model)
names(results) <- model_keys
all_ready <- all(vapply(results, function(result) isTRUE(result$confirmatoryInferenceReady), logical(1)))
output <- list(
  schemaVersion = "mixed-effects-results-1.0",
  generatedAt = format(Sys.time(), tz = "UTC", usetz = TRUE),
  analysisId = spec$analysisId,
  formalAnalysisReady = all_ready && !contract_test,
  contractTest = contract_test,
  modelFitsReady = all_ready,
  source = list(
    preparedDirectory = basename(prepared_dir),
    sourceDatasetSha256 = manifest$sourceDatasetSha256,
    modelSpecSha256 = manifest$modelSpecSha256,
    provenance = manifest$provenance
  ),
  confirmatory = list(
    endpoint = spec$confirmatory$primaryEndpoint,
    contrasts = primary_contrasts,
    multiplicity = spec$confirmatory$multiplicity,
    replicationRule = spec$confirmatory$replicationRule
  ),
  models = results,
  runtime = as.list(current_versions),
  sessionInfo = capture.output(utils::sessionInfo()),
  notes = c(
    "Primary inference is separate for each frozen model identity.",
    if (contract_test) "SYNTHETIC CONTRACT TEST ONLY: no empirical inference is permitted." else "Formal frozen analysis mode.",
    "A singular, non-converged or non-estimable primary fit is reported as a diagnostic failure, not repaired by changing the endpoint.",
    "The template-fixed model is a registered sensitivity analysis and never silently replaces the primary model."
  )
)

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
temporary_output <- paste0(output_path, ".tmp-", Sys.getpid())
jsonlite::write_json(output, temporary_output, auto_unbox = TRUE, pretty = TRUE, na = "null", null = "null")
if (!file.rename(temporary_output, output_path)) {
  stop("could not atomically publish mixed-effects output", call. = FALSE)
}
cat(jsonlite::toJSON(list(
  written = output_path,
  formalAnalysisReady = all_ready && !contract_test,
  contractTest = contract_test,
  modelFitsReady = all_ready
), auto_unbox = TRUE), "\n")
