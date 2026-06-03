cmake_minimum_required(VERSION 3.16)
project(YmodemSender VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 COMPONENTS Widgets SerialPort QUIET)
if(NOT Qt6_FOUND)
    find_package(Qt5 5.15 REQUIRED COMPONENTS Widgets SerialPort)
    set(QT_VERSION_MAJOR 5)
else()
    set(QT_VERSION_MAJOR 6)
endif()

set(SOURCES
    main.cpp
    mainwindow.cpp
    ymodem.cpp
)

set(HEADERS
    mainwindow.h
    ymodem.h
)

add_executable(${PROJECT_NAME} ${SOURCES} ${HEADERS})

if(QT_VERSION_MAJOR EQUAL 6)
    target_link_libraries(${PROJECT_NAME} Qt6::Widgets Qt6::SerialPort)
else()
    target_link_libraries(${PROJECT_NAME} Qt5::Widgets Qt5::SerialPort)
endif()
