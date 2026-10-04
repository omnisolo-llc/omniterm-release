# First input: public Rust source. Second input: captured contract output.
# Emit only declared public function names and Rust's fixed compiler error codes.
FNR == NR {
    line = $0
    if (line ~ /^[ \t]*(pub[ \t]+)?(async[ \t]+)?fn[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*\(/) {
        sub(/^[ \t]*(pub[ \t]+)?(async[ \t]+)?fn[ \t]+/, "", line)
        sub(/[ \t]*\(.*/, "", line)
        if (length(line) <= 128) known[line] = 1
    }
    next
}
{
    sub(/\r$/, "")
    if (emitted >= 20) next
    if ($0 ~ /^test [A-Za-z0-9_:]+ \.\.\. FAILED$/) {
        name = $2
        sub(/^.*::/, "", name)
        if (known[name] && !seen[name]++) {
            print "Native contract failed test: " name
            emitted++
        }
    } else if ($0 ~ /^Native boundary result: (orphan|timeout) (success|timeout|stream|start|group|resume|failed|other|escaped|clean)$/) {
        if (!seen[$0]++) {
            print $0
            emitted++
        }
    } else if ($0 ~ /^error\[E[0-9][0-9][0-9][0-9]\]:/) {
        code = substr($0, 7, 5)
        if (!seen[code]++) {
            print "Native compiler diagnostic: " code
            emitted++
        }
    }
}
