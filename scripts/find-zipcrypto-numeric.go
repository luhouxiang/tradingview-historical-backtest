// Command find-zipcrypto-numeric quickly filters numeric ZipCrypto passwords.
// Every reported candidate passes the ZIP password byte and a 4 KiB DEFLATE
// probe; callers must still perform a complete CRC verification.
package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"compress/flate"
	"flag"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
)

const (
	encryptionHeaderSize = 12
	probeEncryptedBytes  = 64 * 1024
	probePlainBytes      = 4 * 1024
	quickEncryptedBytes  = 512
	quickPlainBytes      = 128
	workChunk            = 2048
)

var crcTable = crc32.MakeTable(crc32.IEEE)

type cryptoState struct {
	key0 uint32
	key1 uint32
	key2 uint32
}

func crcByte(crc uint32, value byte) uint32 {
	return (crc >> 8) ^ crcTable[byte(crc)^value]
}

func newCryptoState(password []byte) cryptoState {
	state := cryptoState{key0: 0x12345678, key1: 0x23456789, key2: 0x34567890}
	for _, value := range password {
		state.update(value)
	}
	return state
}

func (state *cryptoState) update(plain byte) {
	state.key0 = crcByte(state.key0, plain)
	state.key1 = (state.key1+(state.key0&0xff))*134775813 + 1
	state.key2 = crcByte(state.key2, byte(state.key1>>24))
}

func (state *cryptoState) decrypt(cipher byte) byte {
	temporary := state.key2 | 2
	plain := cipher ^ byte((temporary*(temporary^1))>>8)
	state.update(plain)
	return plain
}

func decimalPassword(number uint64, width int, buffer []byte) []byte {
	for index := width - 1; index >= 0; index-- {
		buffer[index] = byte(number%10) + '0'
		number /= 10
	}
	return buffer[:width]
}

func charsetPassword(number uint64, width int, charset string, buffer []byte) []byte {
	base := uint64(len(charset))
	for index := width - 1; index >= 0; index-- {
		buffer[index] = charset[number%base]
		number /= base
	}
	return buffer[:width]
}

func deflateProbe(
	state cryptoState, encrypted []byte, encryptedLimit, plainLimit int,
) bool {
	if encryptedLimit > len(encrypted) {
		encryptedLimit = len(encrypted)
	}
	compressed := make([]byte, encryptedLimit)
	for index, cipher := range encrypted[:encryptedLimit] {
		compressed[index] = state.decrypt(cipher)
	}
	reader := flate.NewReader(bytes.NewReader(compressed))
	defer reader.Close()
	plain := make([]byte, plainLimit)
	_, err := io.ReadFull(reader, plain)
	return err == nil
}

func passesProbe(password, encrypted []byte, checkByte byte) bool {
	state := newCryptoState(password)
	var last byte
	for _, cipher := range encrypted[:encryptionHeaderSize] {
		last = state.decrypt(cipher)
	}
	if last != checkByte {
		return false
	}

	payload := encrypted[encryptionHeaderSize:]
	if !deflateProbe(state, payload, quickEncryptedBytes, quickPlainBytes) {
		return false
	}
	return deflateProbe(state, payload, probeEncryptedBytes, probePlainBytes)
}

func encryptedProbe(path string) ([]byte, byte, error) {
	reader, err := zip.OpenReader(path)
	if err != nil {
		return nil, 0, err
	}
	defer reader.Close()

	var selected *zip.File
	for _, file := range reader.File {
		if file.FileInfo().IsDir() || file.Flags&1 == 0 {
			continue
		}
		if selected == nil || file.CompressedSize64 < selected.CompressedSize64 {
			selected = file
		}
	}
	if selected == nil {
		return nil, 0, fmt.Errorf("ZIP 中没有加密文件")
	}
	if selected.Method != zip.Deflate {
		return nil, 0, fmt.Errorf("仅支持 DEFLATE ZipCrypto，当前方法为 %d", selected.Method)
	}
	offset, err := selected.DataOffset()
	if err != nil {
		return nil, 0, err
	}
	probeSize := uint64(encryptionHeaderSize + probeEncryptedBytes)
	if probeSize > selected.CompressedSize64 {
		probeSize = selected.CompressedSize64
	}
	if probeSize <= encryptionHeaderSize {
		return nil, 0, fmt.Errorf("加密成员过短，无法执行探测")
	}

	handle, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer handle.Close()
	encrypted := make([]byte, probeSize)
	if _, err := handle.ReadAt(encrypted, offset); err != nil {
		return nil, 0, err
	}
	checkByte := byte(selected.CRC32 >> 24)
	if selected.Flags&8 != 0 {
		checkByte = byte(selected.ModifiedTime >> 8)
	}
	return encrypted, checkByte, nil
}

func filterWordlist(
	path string, workers int, encrypted []byte, checkByte byte, hits chan<- string,
) error {
	handle, err := os.Open(path)
	if err != nil {
		return err
	}
	defer handle.Close()

	jobs := make(chan string, workers*4)
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for password := range jobs {
				if passesProbe([]byte(password), encrypted, checkByte) {
					hits <- password
				}
			}
		}()
	}

	scanner := bufio.NewScanner(handle)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	for scanner.Scan() {
		password := scanner.Text()
		if password != "" {
			jobs <- password
		}
	}
	close(jobs)
	wait.Wait()
	return scanner.Err()
}

func filterNumeric(
	start, end uint64,
	width, workers int,
	encrypted []byte,
	checkByte byte,
	hits chan<- string,
) {
	var next atomic.Uint64
	next.Store(start)
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			buffer := make([]byte, width)
			for {
				chunkStart := next.Add(workChunk) - workChunk
				if chunkStart > end {
					return
				}
				chunkEnd := chunkStart + workChunk - 1
				if chunkEnd > end {
					chunkEnd = end
				}
				for number := chunkStart; number <= chunkEnd; number++ {
					password := decimalPassword(number, width, buffer)
					if passesProbe(password, encrypted, checkByte) {
						hits <- string(password)
					}
				}
			}
		}()
	}
	wait.Wait()
}

func filterCharsetLength(
	charset string,
	length, workers int,
	encrypted []byte,
	checkByte byte,
	hits chan<- string,
) error {
	prefixLength := 2
	if length < prefixLength {
		prefixLength = length
	}
	prefixCount := 1
	var positions [256]int
	for index := range len(charset) {
		positions[charset[index]] = index
	}
	for range prefixLength {
		prefixCount *= len(charset)
	}
	jobs := make(chan []byte, workers*2)
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			buffer := make([]byte, length)
			for prefix := range jobs {
				copy(buffer, prefix)
				for index := prefixLength; index < length; index++ {
					buffer[index] = charset[0]
				}
				for {
					password := buffer[:length]
					if passesProbe(password, encrypted, checkByte) {
						hits <- string(password)
					}
					index := length - 1
					for index >= prefixLength {
						position := positions[buffer[index]] + 1
						if position < len(charset) {
							buffer[index] = charset[position]
							break
						}
						buffer[index] = charset[0]
						index--
					}
					if index < prefixLength {
						break
					}
				}
			}
		}()
	}
	for number := range prefixCount {
		prefix := make([]byte, prefixLength)
		charsetPassword(uint64(number), prefixLength, charset, prefix)
		jobs <- prefix
	}
	close(jobs)
	wait.Wait()
	return nil
}

func printableASCIICharset() string {
	charset := "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	for value := byte(33); value <= 126; value++ {
		if bytes.IndexByte([]byte(charset), value) < 0 {
			charset += string(value)
		}
	}
	return charset
}

func main() {
	zipPath := flag.String("zip", "", "encrypted ZIP path")
	wordlist := flag.String("wordlist", "", "UTF-8/ASCII password file, one per line")
	charset := flag.String("charset", "", "brute-force byte characters")
	printable := flag.Bool("printable", false, "use all 94 non-space printable ASCII bytes")
	minLength := flag.Int("min-length", 1, "minimum charset password length")
	maxLength := flag.Int("max-length", 5, "maximum charset password length")
	start := flag.Uint64("start", 0, "inclusive numeric start")
	end := flag.Uint64("end", 999999, "inclusive numeric end")
	width := flag.Int("width", 6, "zero-padded password width")
	workers := flag.Int("workers", runtime.NumCPU(), "parallel workers")
	flag.Parse()
	if *printable {
		*charset = printableASCIICharset()
	}
	if *zipPath == "" || *end < *start || *width < 1 || *width > 19 || *workers < 1 ||
		*minLength < 1 || *maxLength < *minLength {
		flag.Usage()
		os.Exit(2)
	}

	encrypted, checkByte, err := encryptedProbe(*zipPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}

	hits := make(chan string, *workers)
	go func() {
		if *wordlist != "" {
			if err := filterWordlist(*wordlist, *workers, encrypted, checkByte, hits); err != nil {
				fmt.Fprintln(os.Stderr, err)
			}
		} else if *charset != "" {
			for length := *minLength; length <= *maxLength; length++ {
				if err := filterCharsetLength(
					*charset, length, *workers, encrypted, checkByte, hits,
				); err != nil {
					fmt.Fprintln(os.Stderr, err)
					break
				}
			}
		} else {
			filterNumeric(*start, *end, *width, *workers, encrypted, checkByte, hits)
		}
		close(hits)
	}()

	var candidates []string
	for candidate := range hits {
		candidates = append(candidates, candidate)
	}
	sort.Strings(candidates)
	for _, candidate := range candidates {
		fmt.Println(candidate)
	}
}
