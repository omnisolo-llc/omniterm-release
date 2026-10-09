# First input: public Rust source and target markers. Second: private output.
# Emit only whitelisted identifiers, fixed codes and closed boundary results.
function record(message) {
    if (emitted < 20) messages[++emitted] = message
}
FNR == NR {
    line = $0
    if (line ~ /^[ \t]*(pub[ \t]+)?(async[ \t]+)?fn[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*\(/) {
        sub(/^[ \t]*(pub[ \t]+)?(async[ \t]+)?fn[ \t]+/, "", line)
        sub(/[ \t]*\(.*/, "", line)
        if (length(line) <= 128) known[line] = 1
    } else if (line ~ /^Native public target: [A-Za-z_][A-Za-z0-9_-]*$/) {
        target = $4
        if (length(target) <= 128) targets[target] = 1
    }
    next
}
{
    sub(/\r$/, "")
    if ($0 ~ /^[ \t]*Running[ \t]+/) {
        target = $0
        sub(/^[ \t]*Running[ \t]+(unittests[ \t]+)?/, "", target)
        sub(/[ \t]+\(.*/, "", target)
        last_target = ""
        last_test = ""
        if (target ~ /^(tests|src)\/[A-Za-z0-9_\/-]+\.rs$/) {
            sub(/^.*\//, "", target)
            sub(/\.rs$/, "", target)
            if (targets[target]) last_target = target
        }
    }
    if ($0 ~ /^test [A-Za-z0-9_:]+ \.\.\./) {
        name = $2
        sub(/^.*::/, "", name)
        if (known[name]) last_test = name
    }
    if (emitted >= 20) next
    if ($0 ~ /^test [A-Za-z0-9_:]+ \.\.\. FAILED$/) {
        name = $2
        sub(/^.*::/, "", name)
        if (known[name] && !seen[name]++) {
            record("Native contract failed test: " name)
        }
    } else if ($0 ~ /^Native boundary result: (orphan|timeout) (success|timeout|stream|start|group|resume|failed|other|escaped|clean)$/) {
        if (!seen[$0]++) {
            record($0)
        }
    } else if ($0 ~ /^error\[E[0-9][0-9][0-9][0-9]\]:/) {
        code = substr($0, 7, 5)
        if (!seen[code]++) {
            record("Native compiler diagnostic: " code)
        }
    }
}
END {
    budget = 20
    if (last_target != "") {
        print "Native contract last Cargo test target: " last_target
        budget--
    }
    if (last_test != "") {
        print "Native contract last observed test: " last_test
        budget--
    }
    for (i = 1; i <= emitted && i <= budget; i++) print messages[i]
}
