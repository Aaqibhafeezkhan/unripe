module example.com/fixture

go 1.22

toolchain go1.22.1

require (
	github.com/BurntSushi/toml v1.3.2
	github.com/old/thing v1.0.0
	example.com/local v0.0.0-00010101000000-000000000000
	golang.org/x/sync v0.6.0
	golang.org/x/text v0.14.0 // indirect
)

require github.com/pkg/errors v0.9.1 // indirect

replace github.com/old/thing => github.com/new/thing v1.2.0

replace example.com/local => ./local

exclude golang.org/x/sync v0.5.0

retract v0.1.0 // published by mistake
