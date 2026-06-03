// Apache Thrift IDL.
// Regenerate Python stubs after editing: ./gen.sh
//
// The Thrift namespace is intentionally generic ("tsvc") rather than
// the Python package name — and not a Thrift reserved word like
// "service". This keeps generated identifiers free of the literal
// token pygen.sh substitutes when scaffolding a new project, so the
// generated Python stays consistent across a rename.

namespace py tsvc

service PingService {
  string Ping()
  string Echo(1: string message)
}
