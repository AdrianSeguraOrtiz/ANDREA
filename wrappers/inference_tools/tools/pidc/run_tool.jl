#!/usr/bin/env julia

using Distributed
using JSON
using NetworkInference

const NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
const NETWORKINFERENCE_REF = get(
    ENV,
    "NETWORKINFERENCE_REF",
    "e5a3de323127f002e57bbd91c834f7739939ba0e",
)
const SUPPORTED_MODES = Set(["global", "group_emulated"])

struct RuntimeArgs
    input::String
    params::String
    extra::String
    output_dir::String
    threads::Int
end

struct ExpressionInput
    genes::Vector{String}
    columns::Vector{String}
    values::Vector{Vector{Float64}}
end

struct ResolvedParams
    discretizer::String
    estimator::String
    number_of_bins::Int
    base::Float64
end

function parse_args(argv::Vector{String})::RuntimeArgs
    values = Dict{String,String}()
    i = 1
    while i <= length(argv)
        key = argv[i]
        if !startswith(key, "--")
            error("Unknown argument: $key")
        end
        if i == length(argv) || startswith(argv[i + 1], "--")
            error("Missing value for $key")
        end
        values[key[3:end]] = argv[i + 1]
        i += 2
    end

    required = ["input", "params", "extra", "output-dir", "threads"]
    missing = [key for key in required if !haskey(values, key)]
    if !isempty(missing)
        error("Missing required arguments: $(join(missing, ", "))")
    end

    threads = try
        parse(Int, values["threads"])
    catch
        error("--threads must be a positive integer.")
    end
    if threads < 1
        error("--threads must be a positive integer.")
    end
    if threads > 8
        error("--threads must be <= 8 for PIDC, matching toolspec.runtime_resources.threading.max_threads.")
    end

    return RuntimeArgs(
        values["input"],
        values["params"],
        values["extra"],
        values["output-dir"],
        threads,
    )
end

function atomic_write(path::String, content::AbstractString)
    mkpath(dirname(path))
    tmp = path * ".tmp"
    open(tmp, "w") do io
        write(io, content)
    end
    mv(tmp, path; force=true)
end

function write_json(path::String, payload)
    atomic_write(path, JSON.json(payload, 2) * "\n")
end

function write_progress(
    progress_path::String;
    status::String,
    percent::Integer,
    phase::String,
    message::String,
    error_message::Union{Nothing,String}=nothing,
    completed::Union{Nothing,Integer}=nothing,
    total::Union{Nothing,Integer}=nothing,
)
    payload = Dict{String,Any}(
        "status" => status,
        "phase" => phase,
        "percent" => max(0, min(100, Int(percent))),
        "message" => message,
        "timestamp" => time(),
    )
    if error_message !== nothing
        payload["error"] = error_message
    end
    if completed !== nothing
        payload["completed"] = completed
    end
    if total !== nothing
        payload["total"] = total
    end
    write_json(progress_path, payload)
end

function validate_runtime_inputs(args::RuntimeArgs)
    if !isfile(args.input)
        error("Input expression file not found: $(args.input)")
    end
    if !isfile(args.params)
        error("Params file not found: $(args.params)")
    end
    if !isdir(args.extra)
        error("Extra input directory not found: $(args.extra)")
    end
    mkpath(args.output_dir)
end

function load_json_object(path::String, label::String)
    payload = JSON.parsefile(path)
    if !(payload isa Dict)
        error("$label must be a JSON object.")
    end
    return payload
end

function get_scalar_string(raw::Dict, key::String, default::String, allowed::Set{String})
    value = get(raw, key, default)
    if !(value isa AbstractString)
        error("$key must be a string.")
    end
    value = String(value)
    if !(value in allowed)
        error("$key must be one of: $(join(sort(collect(allowed)), ", ")).")
    end
    return value
end

function get_scalar_int(raw::Dict, key::String, default::Int; min_value::Int)
    value = get(raw, key, default)
    if value isa Bool || !(value isa Number) || !isfinite(Float64(value))
        error("$key must be an integer >= $min_value.")
    end
    rounded = round(Int, value)
    if abs(Float64(value) - rounded) > 1e-9 || rounded < min_value
        error("$key must be an integer >= $min_value.")
    end
    return rounded
end

function get_scalar_float(
    raw::Dict,
    key::String,
    default::Float64;
    min_value::Float64,
    disallow_one::Bool=false,
)
    value = get(raw, key, default)
    if value isa Bool || !(value isa Number)
        error("$key must be a finite number.")
    end
    out = Float64(value)
    if !isfinite(out) || out <= min_value
        error("$key must be > $min_value.")
    end
    if disallow_one && isapprox(out, 1.0; atol=0.0, rtol=0.0)
        error("$key must not be 1 because logarithm base 1 is undefined.")
    end
    return out
end

function resolve_params(path::String)::ResolvedParams
    raw = load_json_object(path, "params.json")
    expected = Set(["discretizer", "estimator", "number_of_bins", "base"])
    unknown = sort([String(key) for key in keys(raw) if !(String(key) in expected)])
    if !isempty(unknown)
        @warn "Ignoring unknown PIDC params" unknown
    end

    discretizer = get_scalar_string(
        raw,
        "discretizer",
        "bayesian_blocks",
        Set(["bayesian_blocks", "uniform_width", "uniform_count"]),
    )
    estimator = get_scalar_string(
        raw,
        "estimator",
        "maximum_likelihood",
        Set(["maximum_likelihood", "dirichlet", "shrinkage"]),
    )
    number_of_bins = get_scalar_int(raw, "number_of_bins", 10; min_value=1)
    base = get_scalar_float(raw, "base", 2.0; min_value=0.0, disallow_one=true)
    return ResolvedParams(discretizer, estimator, number_of_bins, base)
end

function load_execution_mode(params_path::String, extra_dir::String)::String
    execution_path = joinpath(dirname(params_path), "execution.json")
    if !isfile(execution_path)
        return "global"
    end
    raw = load_json_object(execution_path, "execution.json")
    mode_value = get(raw, "mode", "global")
    if !(mode_value isa AbstractString)
        error("execution.mode must be a string.")
    end
    mode = String(mode_value)
    if !(mode in SUPPORTED_MODES)
        error("PIDC supports only execution.mode=global or group_emulated.")
    end
    if mode == "group_emulated" && !isfile(joinpath(extra_dir, "groups.tsv"))
        error("groups.tsv is required in --extra when execution.mode=group_emulated.")
    end
    return mode
end

function find_duplicates(values::Vector{String})::Vector{String}
    seen = Set{String}()
    duplicated = Set{String}()
    for value in values
        if value in seen
            push!(duplicated, value)
        end
        push!(seen, value)
    end
    return sort(collect(duplicated))
end

function split_tsv_line(line::String)::Vector{String}
    return split(chomp(line), '\t'; keepempty=true)
end

function read_expression(path::String)::ExpressionInput
    lines = readlines(path)
    if isempty(lines)
        error("expression.tsv is empty.")
    end
    header = split_tsv_line(lines[1])
    if length(header) < 2
        error("expression.tsv must have a gene column and at least one expression column.")
    end
    columns = String.(header[2:end])
    if any(isempty, columns)
        error("expression.tsv contains an empty expression column identifier.")
    end
    duplicated_columns = find_duplicates(columns)
    if !isempty(duplicated_columns)
        error("expression.tsv contains duplicated expression column identifiers: $(join(duplicated_columns, ", "))")
    end

    genes = String[]
    values = Vector{Float64}[]
    expected_width = length(header)
    for (line_number, line) in enumerate(lines[2:end])
        fields = split_tsv_line(line)
        if length(fields) != expected_width
            error("expression.tsv line $(line_number + 1) has $(length(fields)) columns; expected $expected_width.")
        end
        gene_id = fields[1]
        if isempty(gene_id)
            error("expression.tsv line $(line_number + 1) has an empty gene identifier.")
        end
        row = Float64[]
        for raw_value in fields[2:end]
            value = try
                parse(Float64, raw_value)
            catch
                error("expression.tsv line $(line_number + 1) contains a non-numeric expression value: $raw_value")
            end
            if !isfinite(value)
                error("expression.tsv line $(line_number + 1) contains a non-finite expression value: $raw_value")
            end
            push!(row, value)
        end
        push!(genes, gene_id)
        push!(values, row)
    end

    if length(genes) < 3
        error("PIDC requires at least three genes because the algorithm scores gene triplets.")
    end
    if length(columns) < 2
        error("PIDC requires at least two expression columns.")
    end
    duplicated_genes = find_duplicates(genes)
    if !isempty(duplicated_genes)
        error("expression.tsv contains duplicated gene identifiers: $(join(duplicated_genes, ", "))")
    end
    return ExpressionInput(genes, columns, values)
end

function make_aliases(genes::Vector{String})::Dict{String,String}
    width = max(6, ndigits(length(genes)))
    aliases = Dict{String,String}()
    for (idx, gene) in enumerate(genes)
        aliases[gene] = "pidc_gene_" * lpad(string(idx), width, '0')
    end
    return aliases
end

function inverse_aliases(alias_by_gene::Dict{String,String})::Dict{String,String}
    out = Dict{String,String}()
    for (gene, alias) in alias_by_gene
        out[alias] = gene
    end
    return out
end

function write_gene_alias_map(path::String, genes::Vector{String}, alias_by_gene::Dict{String,String})
    open(path, "w") do io
        write(io, "alias\tgene_id\n")
        for gene in genes
            write(io, alias_by_gene[gene], '\t', gene, '\n')
        end
    end
end

function write_upstream_expression(path::String, expression::ExpressionInput, alias_by_gene::Dict{String,String})
    open(path, "w") do io
        write(io, "node")
        for col in expression.columns
            write(io, '\t', col)
        end
        write(io, '\n')
        for (idx, gene) in enumerate(expression.genes)
            write(io, alias_by_gene[gene])
            for value in expression.values[idx]
                write(io, '\t', string(value))
            end
            write(io, '\n')
        end
    end
end

function configure_processes(threads::Int, log_path::String)
    if threads <= 1
        return 1
    end
    workers_to_add = threads - 1
    open(log_path, "a") do io
        println(io, "Adding $workers_to_add Julia worker process(es) for PIDC.")
    end
    addprocs(workers_to_add)
    return nprocs()
end

function load_networkinference_everywhere()
    for pid in setdiff(workers(), [myid()])
        remotecall_wait(Core.eval, pid, Main, :(using NetworkInference))
    end
end

function infer_pidc(upstream_expression_path::String, params::ResolvedParams)
    nodes = NetworkInference.get_nodes(
        upstream_expression_path;
        delim='\t',
        discretizer=params.discretizer,
        estimator=params.estimator,
        number_of_bins=params.number_of_bins,
    )
    return NetworkInference.InferredNetwork(
        NetworkInference.PIDCNetworkInference(),
        nodes;
        estimator=params.estimator,
        base=params.base,
    )
end

function edge_original_ids(edge, gene_by_alias::Dict{String,String})::Tuple{String,String}
    alias_source = String(edge.nodes[1].label)
    alias_target = String(edge.nodes[2].label)
    if !haskey(gene_by_alias, alias_source) || !haskey(gene_by_alias, alias_target)
        error("Upstream returned an unknown gene alias: $alias_source, $alias_target")
    end
    return gene_by_alias[alias_source], gene_by_alias[alias_target]
end

function csv_escape(value)::String
    text = string(value)
    if occursin(',', text) || occursin('"', text) || occursin('\n', text) || occursin('\r', text)
        return "\"" * replace(text, "\"" => "\"\"") * "\""
    end
    return text
end

function convert_edges(network, raw_edges_path::String, network_path::String, gene_by_alias::Dict{String,String})::Int
    rows = Vector{Tuple{String,String,Float64}}()
    open(raw_edges_path, "w") do io
        write(io, "source_alias\ttarget_alias\tsource\ttarget\tweight\n")
        for edge in network.edges
            source, target = edge_original_ids(edge, gene_by_alias)
            weight = Float64(edge.weight)
            if !isfinite(weight)
                error("PIDC produced a non-finite weight for ($source, $target).")
            end
            write(
                io,
                String(edge.nodes[1].label), '\t',
                String(edge.nodes[2].label), '\t',
                source, '\t',
                target, '\t',
                string(weight), '\n',
            )
            if source == target || weight <= 0.0
                continue
            end
            push!(rows, (source, target, weight))
        end
    end

    if isempty(rows)
        error("PIDC produced no positive-magnitude non-self edges.")
    end
    sort!(rows; by = row -> (-row[3], row[1], row[2]))

    open(network_path, "w") do io
        write(io, join(NETWORK_COLUMNS, ","), "\n")
        for (source, target, score) in rows
            write(
                io,
                csv_escape(source), ',',
                csv_escape(target), ',',
                string(score),
                ",?,association,global\n",
            )
        end
    end
    return length(rows)
end

function write_config(
    path::String;
    params::ResolvedParams,
    expression::ExpressionInput,
    execution_mode::String,
    requested_threads::Int,
    julia_processes::Int,
)
    payload = Dict{String,Any}(
        "tool" => "pidc",
        "upstream_package" => "NetworkInference.jl",
        "upstream_ref" => NETWORKINFERENCE_REF,
        "execution_mode" => execution_mode,
        "gene_count" => length(expression.genes),
        "expression_column_count" => length(expression.columns),
        "expression_columns" => expression.columns,
        "requested_threads" => requested_threads,
        "julia_processes" => julia_processes,
        "worker_processes" => max(0, julia_processes - 1),
        "params" => Dict{String,Any}(
            "discretizer" => params.discretizer,
            "estimator" => params.estimator,
            "number_of_bins" => params.number_of_bins,
            "base" => params.base,
            "number_of_bins_rule" => params.discretizer == "bayesian_blocks" ?
                "ignored by NetworkInference.jl because bayesian_blocks selects data-dependent bins" :
                "passed to NetworkInference.jl",
        ),
    )
    write_json(path, payload)
end

function run(args::RuntimeArgs)
    validate_runtime_inputs(args)
    raw_dir = joinpath(args.output_dir, "raw")
    work_dir = joinpath(args.output_dir, "work")
    mkpath(raw_dir)
    mkpath(work_dir)

    progress_path = joinpath(args.output_dir, "progress.json")
    log_path = joinpath(args.output_dir, "pidc.log")
    network_path = joinpath(args.output_dir, "network.csv")
    raw_edges_path = joinpath(raw_dir, "pidc_edges.tsv")
    config_path = joinpath(raw_dir, "pidc_config.json")
    alias_map_path = joinpath(raw_dir, "gene_alias_map.tsv")
    upstream_expression_path = joinpath(work_dir, "pidc_expression.tsv")

    write_progress(
        progress_path;
        status="running",
        percent=0,
        phase="init",
        message="Initializing PIDC wrapper",
    )

    params = resolve_params(args.params)
    execution_mode = load_execution_mode(args.params, args.extra)

    write_progress(
        progress_path;
        status="running",
        percent=10,
        phase="load_input",
        message="Loading expression matrix",
    )
    expression = read_expression(args.input)
    alias_by_gene = make_aliases(expression.genes)
    gene_by_alias = inverse_aliases(alias_by_gene)
    write_gene_alias_map(alias_map_path, expression.genes, alias_by_gene)
    write_upstream_expression(upstream_expression_path, expression, alias_by_gene)

    open(log_path, "w") do io
        println(io, "PIDC wrapper starting")
        println(io, "NetworkInference.jl ref: $NETWORKINFERENCE_REF")
        println(io, "execution_mode: $execution_mode")
        println(io, "genes: $(length(expression.genes)); columns: $(length(expression.columns))")
        println(io, "params: discretizer=$(params.discretizer), estimator=$(params.estimator), number_of_bins=$(params.number_of_bins), base=$(params.base)")
    end

    write_progress(
        progress_path;
        status="running",
        percent=20,
        phase="runtime",
        message="Configuring Julia worker processes",
    )
    julia_processes = configure_processes(args.threads, log_path)
    write_config(
        config_path;
        params=params,
        expression=expression,
        execution_mode=execution_mode,
        requested_threads=args.threads,
        julia_processes=julia_processes,
    )

    write_progress(
        progress_path;
        status="running",
        percent=35,
        phase="inference",
        message="Running NetworkInference.jl PIDC",
    )
    network = nothing
    open(log_path, "a") do io
        redirect_stdout(io) do
            redirect_stderr(io) do
                load_networkinference_everywhere()
                network = infer_pidc(upstream_expression_path, params)
            end
        end
    end

    write_progress(
        progress_path;
        status="running",
        percent=90,
        phase="write_output",
        message="Writing raw PIDC edges and network.csv",
    )
    edge_count = convert_edges(network, raw_edges_path, network_path, gene_by_alias)

    write_progress(
        progress_path;
        status="completed",
        percent=100,
        phase="done",
        message="PIDC inference finished",
        completed=edge_count,
        total=edge_count,
    )
end

function main()
    args = parse_args(ARGS)
    progress_path = joinpath(args.output_dir, "progress.json")
    log_path = joinpath(args.output_dir, "pidc.log")
    try
        run(args)
    catch exc
        mkpath(args.output_dir)
        open(log_path, "a") do io
            println(io, "PIDC wrapper failed.")
            showerror(io, exc, catch_backtrace())
            println(io)
        end
        write_progress(
            progress_path;
            status="failed",
            percent=100,
            phase="error",
            message="PIDC inference failed",
            error_message=sprint(showerror, exc),
        )
        rethrow()
    finally
        worker_ids = setdiff(workers(), [myid()])
        if !isempty(worker_ids)
            rmprocs(worker_ids)
        end
    end
end

main()
